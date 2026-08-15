# Contributing to Brainbox

Thanks for your interest. Brainbox is a personal, noncommercial open-source project, and
contributions that fit that spirit are welcome.

## License of contributions

Brainbox is licensed under the **PolyForm Noncommercial License 1.0.0** (see
[LICENSE](LICENSE)). By submitting a contribution (a pull request, patch, or similar) you
agree that your contribution is provided under the same license and that you have the right
to submit it. Commercial use of the project as a whole requires a separate license from the
copyright holder.

## Before you start

- For anything non-trivial, **open an issue first** to discuss the idea. It saves everyone
  time and avoids duplicate work.
- For a **security** issue, do **not** open a public issue — follow [SECURITY.md](SECURITY.md).

## Working on the code

Brainbox is an operating system for an appliance, so a few house rules keep it dependable:

- **Match the surrounding code.** Follow the existing style, naming, and comment density of
  the file you are editing.
- **Fail closed.** New capabilities should be **off by default** and reversible from the
  portal wherever a user would expect a choice. Don't expose anything on the network without
  an explicit opt-in.
- **Keep the boot chain robust.** Nothing a user adds should be able to wedge boot. Optional
  services must degrade to a clean no-op, never a crash loop.
- **No secrets, ever.** Never commit real credentials, private keys, tokens, home paths, or
  local network details. Use obviously-fake fixtures for tests.
- **Compile/parse before you push.** Python must `py_compile`; shell must pass `sh -n` /
  `bash -n`.

## Pull requests

- Keep them focused; one logical change per PR.
- Describe **what** changed and **why**, and how you verified it (tests, a boot on a VM,
  `os/image/acceptance/`, etc.).
- Update the relevant docs/README when you change behavior or defaults.

## Building and testing locally

The image and ISO builders live in [`os/image/`](os/image/), alongside the acceptance checks
(`os/image/acceptance/`). Read each script's header for its build knobs.

Above all: be decent to each other.
