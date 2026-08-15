#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ClientCaps {

    pub webgl2: bool,

    pub webgpu: bool,

    pub cores: u32,

    pub mem_mb: u32,

    pub hw_decode: bool,

    pub native: bool,

    pub throttled: bool,
}

impl ClientCaps {

    pub fn display_only() -> ClientCaps {
        ClientCaps { webgl2: false, webgpu: false, cores: 1, mem_mb: 512, hw_decode: false, native: false, throttled: false }
    }

    pub fn can_composite(&self) -> bool {
        (self.webgl2 || self.webgpu) && !self.throttled
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ServerCaps {

    pub gpu: bool,

    pub gpu_strength: u8,
    pub cores: u32,

    pub mem_headroom_mb: u32,

    pub load_avg1: f32,
}

impl ServerCaps {

    pub fn gpuless(cores: u32, mem_headroom_mb: u32, load_avg1: f32) -> ServerCaps {
        ServerCaps { gpu: false, gpu_strength: 0, cores, mem_headroom_mb, load_avg1 }
    }

    pub fn overloaded(&self) -> bool {

        let per_core = if self.cores > 0 { self.load_avg1 / self.cores as f32 } else { self.load_avg1 };
        per_core > 0.85 || self.mem_headroom_mb < 512
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlacementPolicy {

    Centralized,

    Offload,

    Auto,
}

impl Default for PlacementPolicy {
    fn default() -> PlacementPolicy {
        PlacementPolicy::Auto
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RenderMode {

    ServerComposite,

    ClientComposite,

}

impl RenderMode {
    pub fn as_str(self) -> &'static str {
        match self {
            RenderMode::ServerComposite => "server-composite",
            RenderMode::ClientComposite => "client-composite",
        }
    }
}

const STRONG_GPU: u8 = 60;

pub fn decide_placement(server: &ServerCaps, client: &ClientCaps, policy: PlacementPolicy) -> RenderMode {
    match policy {

        PlacementPolicy::Centralized => RenderMode::ServerComposite,
        PlacementPolicy::Offload => {
            if client.can_composite() { RenderMode::ClientComposite } else { RenderMode::ServerComposite }
        }
        PlacementPolicy::Auto => {

            if !client.can_composite() {
                return RenderMode::ServerComposite;
            }

            if server.gpu && server.gpu_strength >= STRONG_GPU && !server.overloaded() {
                return RenderMode::ServerComposite;
            }

            RenderMode::ClientComposite
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn webgl_client() -> ClientCaps {
        ClientCaps { webgl2: true, webgpu: false, cores: 8, mem_mb: 8192, hw_decode: true, native: false, throttled: false }
    }

    #[test]
    fn gpuless_nas_with_capable_client_offloads() {

        let s = ServerCaps::gpuless(6, 4096, 0.3);
        assert_eq!(decide_placement(&s, &webgl_client(), PlacementPolicy::Auto), RenderMode::ClientComposite);
    }

    #[test]
    fn display_only_client_forces_server_composite() {

        let s = ServerCaps::gpuless(6, 4096, 0.3);
        assert_eq!(decide_placement(&s, &ClientCaps::display_only(), PlacementPolicy::Auto), RenderMode::ServerComposite);
    }

    #[test]
    fn strong_gpu_nas_auto_centralizes() {

        let s = ServerCaps { gpu: true, gpu_strength: 90, cores: 16, mem_headroom_mb: 8192, load_avg1: 1.0 };
        assert_eq!(decide_placement(&s, &webgl_client(), PlacementPolicy::Auto), RenderMode::ServerComposite);
    }

    #[test]
    fn strong_gpu_but_overloaded_sheds_to_capable_client() {

        let s = ServerCaps { gpu: true, gpu_strength: 90, cores: 8, mem_headroom_mb: 8192, load_avg1: 15.0 };
        assert_eq!(decide_placement(&s, &webgl_client(), PlacementPolicy::Auto), RenderMode::ClientComposite);
    }

    #[test]
    fn throttled_client_pulls_compositing_back_to_box() {

        let mut c = webgl_client();
        c.throttled = true;
        let s = ServerCaps::gpuless(6, 4096, 0.3);
        assert_eq!(decide_placement(&s, &c, PlacementPolicy::Auto), RenderMode::ServerComposite);
    }

    #[test]
    fn centralized_policy_always_server_even_gpuless() {

        let s = ServerCaps::gpuless(6, 4096, 0.3);
        assert_eq!(decide_placement(&s, &webgl_client(), PlacementPolicy::Centralized), RenderMode::ServerComposite);
    }

    #[test]
    fn offload_policy_respects_incapable_client() {

        let s = ServerCaps { gpu: true, gpu_strength: 90, cores: 16, mem_headroom_mb: 8192, load_avg1: 1.0 };
        assert_eq!(decide_placement(&s, &ClientCaps::display_only(), PlacementPolicy::Offload), RenderMode::ServerComposite);
        assert_eq!(decide_placement(&s, &webgl_client(), PlacementPolicy::Offload), RenderMode::ClientComposite);
    }

    #[test]
    fn native_power_node_composites_when_box_is_gpuless() {

        let mut c = webgl_client();
        c.native = true;
        c.webgpu = true;
        c.mem_mb = 65536;
        c.cores = 24;
        let s = ServerCaps::gpuless(6, 4096, 0.3);
        assert_eq!(decide_placement(&s, &c, PlacementPolicy::Auto), RenderMode::ClientComposite);
    }
}
