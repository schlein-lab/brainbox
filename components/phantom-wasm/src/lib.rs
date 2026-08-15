use std::collections::HashMap;

use wasm_bindgen::prelude::*;
use web_sys::{
    HtmlCanvasElement, WebGl2RenderingContext as GL, WebGlProgram, WebGlShader, WebGlTexture,
    WebGlUniformLocation,
};

const MAGIC: [u8; 4] = [0x50, 0x53, 0x4E, 0x31];
const HEADER_LEN: usize = 28;
const FLAG_KEYFRAME: u16 = 0x0001;
const SFLAG_HAS_PIXELS: u8 = 0x0001;
const FORMAT_SHM_BGRA: u8 = 0;

#[derive(Clone, Copy, Debug)]
struct Rect {
    x: u16,
    y: u16,
    w: u16,
    h: u16,
}

struct Surface {
    id: u32,
    x: i16,
    y: i16,
    w: u16,
    h: u16,
    stride_px: u16,
    format: u8,
    has_pixels: bool,

    damage: Vec<Rect>,

    pixels_off: usize,
    pixels_len: usize,
}

struct SceneFrame {

    keyframe: bool,
    seat_generation: u32,
    frame_seq: u32,
    screen_w: u16,
    screen_h: u16,
    surfaces: Vec<Surface>,
}

struct Cursor<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> Cursor<'a> {
    fn new(b: &'a [u8]) -> Self {
        Cursor { b, p: 0 }
    }
    #[inline]
    fn remaining(&self) -> usize {
        self.b.len().saturating_sub(self.p)
    }
    #[inline]
    fn take(&mut self, n: usize) -> Result<&'a [u8], String> {
        if self.remaining() < n {
            return Err(format!(
                "short read: need {} at off {}, have {}",
                n,
                self.p,
                self.remaining()
            ));
        }
        let s = &self.b[self.p..self.p + n];
        self.p += n;
        Ok(s)
    }
    #[inline]
    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.take(1)?[0])
    }
    #[inline]
    fn u16(&mut self) -> Result<u16, String> {
        let s = self.take(2)?;
        Ok(u16::from_le_bytes([s[0], s[1]]))
    }
    #[inline]
    fn i16(&mut self) -> Result<i16, String> {
        Ok(self.u16()? as i16)
    }
    #[inline]
    fn u32(&mut self) -> Result<u32, String> {
        let s = self.take(4)?;
        Ok(u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
    }
}

impl SceneFrame {
    fn parse(bytes: &[u8]) -> Result<SceneFrame, String> {
        if bytes.len() < HEADER_LEN {
            return Err(format!("frame too small: {} < {}", bytes.len(), HEADER_LEN));
        }
        let mut c = Cursor::new(bytes);
        let magic = c.take(4)?;
        if magic != MAGIC {
            return Err(format!("bad magic {:?}", magic));
        }
        let version = c.u16()?;
        if version != 1 {
            return Err(format!("unsupported version {}", version));
        }
        let flags = c.u16()?;
        let seat_generation = c.u32()?;
        let frame_seq = c.u32()?;
        let screen_w = c.u16()?;
        let screen_h = c.u16()?;
        let surface_count = c.u16()?;
        let _reserved = c.u16()?;
        let _total_pixel_bytes = c.u32()?;

        let mut surfaces = Vec::with_capacity(surface_count as usize);
        for _ in 0..surface_count {
            let id = c.u32()?;
            let x = c.i16()?;
            let y = c.i16()?;
            let w = c.u16()?;
            let h = c.u16()?;
            let stride_px = c.u16()?;
            let format = c.u8()?;
            let sflags = c.u8()?;
            let damage_count = c.u16()?;
            let mut damage = Vec::with_capacity(damage_count as usize);
            for _ in 0..damage_count {
                let rx = c.u16()?;
                let ry = c.u16()?;
                let rw = c.u16()?;
                let rh = c.u16()?;
                damage.push(Rect {
                    x: rx,
                    y: ry,
                    w: rw,
                    h: rh,
                });
            }
            let pixel_bytes = c.u32()? as usize;

            let pixels_off = c.p;
            let _payload = c.take(pixel_bytes)?;
            surfaces.push(Surface {
                id,
                x,
                y,
                w,
                h,
                stride_px,
                format,
                has_pixels: (sflags & SFLAG_HAS_PIXELS) != 0,
                damage,
                pixels_off,
                pixels_len: pixel_bytes,
            });
        }

        Ok(SceneFrame {
            keyframe: (flags & FLAG_KEYFRAME) != 0,
            seat_generation,
            frame_seq,
            screen_w,
            screen_h,
            surfaces,
        })
    }
}

const VERT_SRC: &str = r#"#version 300 es
precision highp float;
// Unit quad [0,1]x[0,1]; per-surface rect passed as a uniform.
layout(location = 0) in vec2 a_unit;
// u_rect = (x, y, w, h) in screen pixels; u_screen = (screen_w, screen_h).
uniform vec4 u_rect;
uniform vec2 u_screen;
out vec2 v_uv;
void main() {
    // Texture coords: top-left origin (row 0 at top of surface).
    v_uv = a_unit;
    // Pixel position of this vertex within the surface rect.
    vec2 px = u_rect.xy + a_unit * u_rect.zw;
    // Map pixel space -> clip space. Screen origin top-left, +y down.
    vec2 ndc = vec2(
        (px.x / u_screen.x) * 2.0 - 1.0,
        1.0 - (px.y / u_screen.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
}
"#;

const FRAG_SRC: &str = r#"#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 frag;
void main() {
    // Texture holds BGRA bytes uploaded as RGBA; swizzle back to RGBA.
    vec4 texel = texture(u_tex, v_uv);
    frag = texel.bgra;
}
"#;

struct SurfTex {
    tex: WebGlTexture,

    w: i32,
    h: i32,

    x: i32,
    y: i32,
}

#[wasm_bindgen]
pub struct Compositor {
    gl: GL,
    canvas: HtmlCanvasElement,
    program: WebGlProgram,
    u_rect: WebGlUniformLocation,
    u_screen: WebGlUniformLocation,
    u_tex: WebGlUniformLocation,

    textures: HashMap<u32, SurfTex>,

    order: Vec<u32>,
    screen_w: i32,
    screen_h: i32,
    last_gen: u32,
    frames: u64,
}

#[wasm_bindgen]
impl Compositor {

    #[wasm_bindgen(constructor)]
    pub fn new(canvas_id: &str) -> Result<Compositor, JsValue> {

        console_error_panic_hook_lite();

        let window = web_sys::window().ok_or_else(|| js_err("no window"))?;
        let document = window.document().ok_or_else(|| js_err("no document"))?;
        let el = document
            .get_element_by_id(canvas_id)
            .ok_or_else(|| js_err(&format!("no element #{}", canvas_id)))?;
        let canvas: HtmlCanvasElement = el
            .dyn_into::<HtmlCanvasElement>()
            .map_err(|_| js_err(&format!("#{} is not a <canvas>", canvas_id)))?;

        let gl = canvas
            .get_context("webgl2")
            .map_err(|_| js_err("get_context threw"))?
            .ok_or_else(|| js_err("webgl2 not available"))?
            .dyn_into::<GL>()
            .map_err(|_| js_err("context is not WebGL2"))?;

        let program = link_program(&gl, VERT_SRC, FRAG_SRC).map_err(|e| js_err(&e))?;
        gl.use_program(Some(&program));

        let u_rect = gl
            .get_uniform_location(&program, "u_rect")
            .ok_or_else(|| js_err("no u_rect uniform"))?;
        let u_screen = gl
            .get_uniform_location(&program, "u_screen")
            .ok_or_else(|| js_err("no u_screen uniform"))?;
        let u_tex = gl
            .get_uniform_location(&program, "u_tex")
            .ok_or_else(|| js_err("no u_tex uniform"))?;

        let verts: [f32; 12] = [
            0.0, 0.0,
            1.0, 0.0,
            0.0, 1.0,
            0.0, 1.0,
            1.0, 0.0,
            1.0, 1.0,
        ];
        let vao = gl
            .create_vertex_array()
            .ok_or_else(|| js_err("create_vertex_array failed"))?;
        gl.bind_vertex_array(Some(&vao));
        let vbo = gl
            .create_buffer()
            .ok_or_else(|| js_err("create_buffer failed"))?;
        gl.bind_buffer(GL::ARRAY_BUFFER, Some(&vbo));
        unsafe {
            let view = js_sys::Float32Array::view(&verts);
            gl.buffer_data_with_array_buffer_view(GL::ARRAY_BUFFER, &view, GL::STATIC_DRAW);
        }
        gl.vertex_attrib_pointer_with_i32(0, 2, GL::FLOAT, false, 0, 0);
        gl.enable_vertex_attrib_array(0);

        gl.enable(GL::BLEND);
        gl.blend_func(GL::ONE, GL::ONE_MINUS_SRC_ALPHA);

        gl.pixel_storei(GL::UNPACK_ALIGNMENT, 1);

        gl.uniform1i(Some(&u_tex), 0);

        Ok(Compositor {
            gl,
            canvas,
            program,
            u_rect,
            u_screen,
            u_tex,
            textures: HashMap::new(),
            order: Vec::new(),
            screen_w: 0,
            screen_h: 0,
            last_gen: 0,
            frames: 0,
        })
    }

    pub fn on_frame(&mut self, bytes: &[u8]) -> Result<(), JsValue> {
        let frame = SceneFrame::parse(bytes).map_err(|e| js_err(&e))?;

        if frame.seat_generation != self.last_gen {
            self.drop_textures();
            self.last_gen = frame.seat_generation;
        }

        self.ensure_canvas_size(frame.screen_w as i32, frame.screen_h as i32);
        self.upload_and_draw(bytes, &frame)?;
        self.frames = self.frames.wrapping_add(1);
        let _ = frame.frame_seq;
        Ok(())
    }

    pub fn resize(&mut self) {
        let (w, h) = (self.screen_w, self.screen_h);
        if w > 0 && h > 0 {
            self.screen_w = 0;
            self.ensure_canvas_size(w, h);
        }
    }

    #[wasm_bindgen(getter)]
    pub fn frame_count(&self) -> u64 {
        self.frames
    }
}

impl Compositor {
    fn drop_textures(&mut self) {
        for (_, st) in self.textures.drain() {
            self.gl.delete_texture(Some(&st.tex));
        }
        self.order.clear();
    }

    fn ensure_canvas_size(&mut self, w: i32, h: i32) {
        if w == self.screen_w && h == self.screen_h {
            return;
        }
        self.screen_w = w;
        self.screen_h = h;
        if self.canvas.width() != w as u32 {
            self.canvas.set_width(w as u32);
        }
        if self.canvas.height() != h as u32 {
            self.canvas.set_height(h as u32);
        }
        self.gl.viewport(0, 0, w, h);
    }

    fn upload_and_draw(&mut self, frame_bytes: &[u8], frame: &SceneFrame) -> Result<(), JsValue> {

        if frame.keyframe {
            self.apply_keyframe(frame_bytes, frame)?;
        } else {
            self.apply_delta(frame_bytes, frame)?;
        }

        self.paint(frame)
    }

    fn apply_keyframe(&mut self, frame_bytes: &[u8], frame: &SceneFrame) -> Result<(), JsValue> {

        self.order.clear();
        let mut seen: Vec<u32> = Vec::with_capacity(frame.surfaces.len());

        for surf in &frame.surfaces {
            if surf.format != FORMAT_SHM_BGRA || surf.w == 0 || surf.h == 0 {
                continue;
            }
            seen.push(surf.id);
            self.order.push(surf.id);

            if surf.has_pixels && surf.pixels_len > 0 {
                self.upload_full(frame_bytes, surf)?;
            }
            self.update_geom(surf);
        }

        let stale: Vec<u32> = self
            .textures
            .keys()
            .copied()
            .filter(|id| !seen.contains(id))
            .collect();
        for id in stale {
            if let Some(st) = self.textures.remove(&id) {
                self.gl.delete_texture(Some(&st.tex));
            }
        }
        Ok(())
    }

    fn apply_delta(&mut self, frame_bytes: &[u8], frame: &SceneFrame) -> Result<(), JsValue> {
        for surf in &frame.surfaces {
            if surf.format != FORMAT_SHM_BGRA || surf.w == 0 || surf.h == 0 {
                continue;
            }

            if !surf.has_pixels || surf.pixels_len == 0 {
                self.update_geom(surf);
                continue;
            }

            let need_alloc = match self.textures.get(&surf.id) {
                Some(st) => st.w != surf.w as i32 || st.h != surf.h as i32,
                None => true,
            };
            if need_alloc {
                self.alloc_cleared(surf)?;

                if !self.order.contains(&surf.id) {
                    self.order.push(surf.id);
                }
            }

            self.sub_upload_damage(frame_bytes, surf)?;
            self.update_geom(surf);
        }
        Ok(())
    }

    fn paint(&mut self, frame: &SceneFrame) -> Result<(), JsValue> {
        let gl = &self.gl;

        gl.clear_color(0.0, 0.0, 0.0, 1.0);
        gl.clear(GL::COLOR_BUFFER_BIT);

        gl.use_program(Some(&self.program));
        gl.uniform2f(
            Some(&self.u_screen),
            frame.screen_w as f32,
            frame.screen_h as f32,
        );
        gl.uniform1i(Some(&self.u_tex), 0);
        gl.active_texture(GL::TEXTURE0);

        for id in &self.order {
            let st = match self.textures.get(id) {
                Some(t) => t,
                None => continue,
            };
            gl.bind_texture(GL::TEXTURE_2D, Some(&st.tex));
            gl.uniform4f(
                Some(&self.u_rect),
                st.x as f32,
                st.y as f32,
                st.w as f32,
                st.h as f32,
            );
            gl.draw_arrays(GL::TRIANGLES, 0, 6);
        }

        Ok(())
    }

    fn update_geom(&mut self, surf: &Surface) {
        if let Some(st) = self.textures.get_mut(&surf.id) {
            st.x = surf.x as i32;
            st.y = surf.y as i32;

        }
    }

    fn alloc_cleared(&mut self, surf: &Surface) -> Result<(), JsValue> {
        let gl = &self.gl;
        let w = surf.w as i32;
        let h = surf.h as i32;

        if let Some(old) = self.textures.remove(&surf.id) {
            gl.delete_texture(Some(&old.tex));
        }
        let tex = gl
            .create_texture()
            .ok_or_else(|| js_err("create_texture failed"))?;
        gl.bind_texture(GL::TEXTURE_2D, Some(&tex));
        gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_MIN_FILTER, GL::LINEAR as i32);
        gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_MAG_FILTER, GL::LINEAR as i32);
        gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_WRAP_S, GL::CLAMP_TO_EDGE as i32);
        gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_WRAP_T, GL::CLAMP_TO_EDGE as i32);

        gl.tex_image_2d_with_i32_and_i32_and_i32_and_format_and_type_and_opt_u8_array(
            GL::TEXTURE_2D,
            0,
            GL::RGBA as i32,
            w,
            h,
            0,
            GL::RGBA,
            GL::UNSIGNED_BYTE,
            None,
        )
        .map_err(|e| js_err(&format!("cleared texImage2D failed: {:?}", e)))?;
        self.textures.insert(
            surf.id,
            SurfTex {
                tex,
                w,
                h,
                x: surf.x as i32,
                y: surf.y as i32,
            },
        );
        Ok(())
    }

    fn upload_full(&mut self, frame_bytes: &[u8], surf: &Surface) -> Result<(), JsValue> {
        let gl = &self.gl;
        let w = surf.w as i32;
        let h = surf.h as i32;

        let expected = (w as usize) * (h as usize) * 4;
        let off = surf.pixels_off;
        let len = surf.pixels_len;
        if off + len > frame_bytes.len() || len < expected {
            return Err(js_err(&format!(
                "surface {} keyframe payload {} < expected {} (w{} h{})",
                surf.id, len, expected, w, h
            )));
        }
        let px = &frame_bytes[off..off + expected];
        let _ = surf.stride_px;

        let need_alloc = match self.textures.get(&surf.id) {
            Some(st) => st.w != w || st.h != h,
            None => true,
        };

        if need_alloc {
            if let Some(old) = self.textures.remove(&surf.id) {
                gl.delete_texture(Some(&old.tex));
            }
            let tex = gl
                .create_texture()
                .ok_or_else(|| js_err("create_texture failed"))?;
            gl.bind_texture(GL::TEXTURE_2D, Some(&tex));
            gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_MIN_FILTER, GL::LINEAR as i32);
            gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_MAG_FILTER, GL::LINEAR as i32);
            gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_WRAP_S, GL::CLAMP_TO_EDGE as i32);
            gl.tex_parameteri(GL::TEXTURE_2D, GL::TEXTURE_WRAP_T, GL::CLAMP_TO_EDGE as i32);
            gl.tex_image_2d_with_i32_and_i32_and_i32_and_format_and_type_and_opt_u8_array(
                GL::TEXTURE_2D,
                0,
                GL::RGBA as i32,
                w,
                h,
                0,
                GL::RGBA,
                GL::UNSIGNED_BYTE,
                Some(px),
            )
            .map_err(|e| js_err(&format!("texImage2D failed: {:?}", e)))?;
            self.textures.insert(
                surf.id,
                SurfTex {
                    tex,
                    w,
                    h,
                    x: surf.x as i32,
                    y: surf.y as i32,
                },
            );
        } else {

            let st = self.textures.get(&surf.id).unwrap();
            gl.bind_texture(GL::TEXTURE_2D, Some(&st.tex));
            gl.tex_sub_image_2d_with_i32_and_i32_and_u32_and_type_and_opt_u8_array(
                GL::TEXTURE_2D,
                0,
                0,
                0,
                w,
                h,
                GL::RGBA,
                GL::UNSIGNED_BYTE,
                Some(px),
            )
            .map_err(|e| js_err(&format!("keyframe texSubImage2D failed: {:?}", e)))?;
        }

        Ok(())
    }

    fn sub_upload_damage(&mut self, frame_bytes: &[u8], surf: &Surface) -> Result<(), JsValue> {
        let gl = &self.gl;
        let tw = surf.w as i32;
        let th = surf.h as i32;

        let st = match self.textures.get(&surf.id) {
            Some(t) => t,
            None => {

                return Err(js_err(&format!(
                    "delta surface {} has no texture",
                    surf.id
                )));
            }
        };
        gl.bind_texture(GL::TEXTURE_2D, Some(&st.tex));

        let payload_end = surf.pixels_off + surf.pixels_len;
        if payload_end > frame_bytes.len() {
            return Err(js_err(&format!(
                "delta surface {} payload overruns frame",
                surf.id
            )));
        }

        let mut cur = surf.pixels_off;
        for r in &surf.damage {
            let rx = r.x as i32;
            let ry = r.y as i32;
            let rw = r.w as i32;
            let rh = r.h as i32;
            if rw <= 0 || rh <= 0 {
                continue;
            }

            if rx < 0 || ry < 0 || rx + rw > tw || ry + rh > th {
                return Err(js_err(&format!(
                    "delta surface {} rect ({},{},{},{}) out of bounds {}x{}",
                    surf.id, rx, ry, rw, rh, tw, th
                )));
            }
            let need = (rw as usize) * (rh as usize) * 4;
            if cur + need > payload_end {
                return Err(js_err(&format!(
                    "delta surface {} rect payload short: need {} have {}",
                    surf.id,
                    need,
                    payload_end.saturating_sub(cur)
                )));
            }
            let px = &frame_bytes[cur..cur + need];
            cur += need;
            gl.tex_sub_image_2d_with_i32_and_i32_and_u32_and_type_and_opt_u8_array(
                GL::TEXTURE_2D,
                0,
                rx,
                ry,
                rw,
                rh,
                GL::RGBA,
                GL::UNSIGNED_BYTE,
                Some(px),
            )
            .map_err(|e| js_err(&format!("delta texSubImage2D failed: {:?}", e)))?;
        }

        Ok(())
    }
}

fn js_err(msg: &str) -> JsValue {
    JsValue::from_str(msg)
}

fn compile_shader(gl: &GL, kind: u32, src: &str) -> Result<WebGlShader, String> {
    let sh = gl
        .create_shader(kind)
        .ok_or_else(|| "create_shader failed".to_string())?;
    gl.shader_source(&sh, src);
    gl.compile_shader(&sh);
    if gl
        .get_shader_parameter(&sh, GL::COMPILE_STATUS)
        .as_bool()
        .unwrap_or(false)
    {
        Ok(sh)
    } else {
        let log = gl
            .get_shader_info_log(&sh)
            .unwrap_or_else(|| "unknown shader error".into());
        Err(format!("shader compile error: {}", log))
    }
}

fn link_program(gl: &GL, vs: &str, fs: &str) -> Result<WebGlProgram, String> {
    let v = compile_shader(gl, GL::VERTEX_SHADER, vs)?;
    let f = compile_shader(gl, GL::FRAGMENT_SHADER, fs)?;
    let prog = gl
        .create_program()
        .ok_or_else(|| "create_program failed".to_string())?;
    gl.attach_shader(&prog, &v);
    gl.attach_shader(&prog, &f);
    gl.link_program(&prog);
    if gl
        .get_program_parameter(&prog, GL::LINK_STATUS)
        .as_bool()
        .unwrap_or(false)
    {
        Ok(prog)
    } else {
        let log = gl
            .get_program_info_log(&prog)
            .unwrap_or_else(|| "unknown link error".into());
        Err(format!("program link error: {}", log))
    }
}

fn console_error_panic_hook_lite() {
    use std::sync::Once;
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        std::panic::set_hook(Box::new(|info| {
            let msg = format!("phantom-wasm panic: {}", info);
            web_sys::console::error_1(&JsValue::from_str(&msg));
        }));
    });
}
