# Building

BrainBox ships as a self-contained appliance image; you do not install it onto a running
Linux the way you would a package.

## Appliance image & installer ISO

The immutable A/B disk image and the BIOS/UEFI installer ISO are produced from `os/image`.
The build assembles a pinned root filesystem, bakes in the engine and portal, and emits a
disk image plus a hybrid installer ISO that boots straight into first-run setup — no host
OS configuration required. See the build scripts under `os/image`.

## Native components

- **`os/pn-vmm`** (the microVM monitor) and **`components/phantom`** (the compositor) are
  Rust crates — build with `cargo build --release` inside each crate.
- **`os/init/pn-init`** is C — see its `Makefile`.
- **`installer`** and **`gateway`** are Go — `go build ./...`.

## Portal (development)

The cockpit portal, `cockpit/server/brainbox-portal`, is stdlib-only Python 3 and can be
run directly for development. It serves the first-run setup wizard and the full web
cockpit. The single-page frontend lives in `cockpit/server/webapp`.

## Cells

Agent sessions run as microVMs built root-free by the `build_cell_*` tools in `os/pn-vmm`.

## License

Source-available under PolyForm Noncommercial — see `LICENSE`.
