
export const IFACE_XML = `
<node>
  <interface name="org.gnome.Phantom">
    <method name="Snapshot">
      <arg type="ay" direction="out" name="png"/>
    </method>
    <method name="ActivateWindow">
      <arg type="t" direction="in" name="id"/>
      <arg type="u" direction="in" name="timestamp"/>
      <arg type="b" direction="out" name="raised"/>
    </method>
    <method name="ListWindows">
      <arg type="s" direction="out" name="windowsJson"/>
    </method>
    <method name="SetStage">
      <arg type="s" direction="in" name="stage"/>
      <arg type="b" direction="out" name="applied"/>
    </method>
    <method name="DebugState">
      <arg type="s" direction="out" name="stateJson"/>
    </method>
    <method name="ToggleLlm">
      <arg type="b" direction="out" name="shown"/>
    </method>
    <method name="SummonLlm">
      <arg type="b" direction="out" name="ok"/>
    </method>

    <!-- ===================================================================
         STAGE 3 ACT/SENSE (FUSION-FRAMEWORK §3/§4, ARCH §4). Every ACT verb
         passes the ModeController gate FIRST.

         (a) Funktionsbus — the PRIMARY act plane (focus-free, no pick/raise,
             reaches hidden/occluded/off-workspace apps). Out-of-process AT-SPI
             helper; the verb stays here on org.gnome.Phantom.
    -->
    <method name="InvokeAction">
      <arg type="s" direction="in" name="appHint"/>
      <arg type="s" direction="in" name="selector"/>
      <arg type="s" direction="in" name="action"/>
      <arg type="s" direction="out" name="resultJson"/>
    </method>
    <method name="WriteWidget">
      <arg type="s" direction="in" name="appHint"/>
      <arg type="s" direction="in" name="selector"/>
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="out" name="resultJson"/>
    </method>
    <method name="ReadWidget">
      <arg type="s" direction="in" name="appHint"/>
      <arg type="s" direction="in" name="selector"/>
      <arg type="s" direction="out" name="resultJson"/>
    </method>
    <method name="ReadTree">
      <arg type="s" direction="in" name="appHint"/>
      <arg type="i" direction="in" name="maxNodes"/>
      <arg type="s" direction="out" name="treeJson"/>
    </method>
    <method name="ListA11yApps">
      <arg type="s" direction="out" name="appsJson"/>
    </method>

    <!-- (b) Clutter inject — the FALLBACK act plane. Valid ONLY for a target
             that is currently VISIBLE AND MAPPED (Wayland pick needs is_mapped;
             injecting at an occluded buffer_rect mis-routes). Guarded. -->
    <method name="Click">
      <arg type="d" direction="in" name="x"/>
      <arg type="d" direction="in" name="y"/>
      <arg type="u" direction="in" name="button"/>
      <arg type="b" direction="out" name="injected"/>
    </method>
    <method name="Type">
      <arg type="s" direction="in" name="text"/>
      <arg type="b" direction="out" name="injected"/>
    </method>

    <!-- (c) Window verbs — direct on the resolved MetaWindow, no synthetic
             input (ARCH §4 "Window-mgmt verbs"). Side-effecting -> gated. -->
    <method name="Minimize">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="Maximize">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="MakeAbove">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="in" name="on"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="MoveToWorkspace">
      <arg type="t" direction="in" name="id"/>
      <arg type="i" direction="in" name="ws"/>
      <arg type="b" direction="out" name="ok"/>
    </method>

    <!-- (d) Sense — coarse focused-window + the live window-tree signal. -->
    <method name="FocusedWindow">
      <arg type="s" direction="out" name="windowJson"/>
    </method>
    <signal name="WindowsChanged">
      <arg type="s" name="reason"/>
    </signal>

    <!-- ===================================================================
         STAGE 4 FUSION v0 (docs/FUSION-FRAMEWORK.md §1-§5). "GUIs kombinierbar
         / Funktionsderivat." MIRROR (clone + input-proxy) is the crash-isolated
         DEFAULT (§2). MERGE/reparent is RESERVED (avoided here, §2 rule). Two
         planes:

         (e) GUI fusion — MIRROR a source window's compositor actor as a
             Clutter.Clone mounted as a phantom tile via the existing chrome
             mount (FUSION §1/§3). The clone auto-repaints (clutter_clone_paint
             shares the source paint node). A transparent reactive InputProxy
             over the clone maps proxy-local coords via the source buffer_rect
             and re-injects through the SAME ClutterVirtualInputDevice path as
             Click/Type — GATED through the ModeController like every act verb
             AND gated to a visible+mapped source (§3 precondition). -->
    <method name="CreateMirror">
      <arg type="t" direction="in" name="sourceId"/>
      <arg type="d" direction="in" name="scale"/>
      <arg type="b" direction="in" name="interactive"/>
      <arg type="s" direction="out" name="resultJson"/>
    </method>
    <method name="DestroyMirror">
      <arg type="s" direction="in" name="mirrorId"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="ListMirrors">
      <arg type="s" direction="out" name="mirrorsJson"/>
    </method>

    <!-- (f) Function fusion — the Funktionsbus RECIPE verb (FUSION §5). Chains
             existing act/sense ops (ReadWidget/InvokeAction/WriteWidget) across
             apps per a small declarative JSON format — the "function-derivation"
             pipeline. Every side-effecting sink passes the ModeController gate. -->
    <method name="RunRecipe">
      <arg type="s" direction="in" name="recipeJson"/>
      <arg type="s" direction="out" name="resultJson"/>
    </method>

    <property name="Stage" type="s" access="read"/>
  </interface>
</node>`;
