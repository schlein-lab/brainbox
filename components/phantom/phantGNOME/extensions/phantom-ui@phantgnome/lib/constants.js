
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';

function _ensurePromisified(obj, name) {
    try {
        Gio._promisify(obj, name);
    } catch (e) {

    }
}
_ensurePromisified(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
_ensurePromisified(Shell.Screenshot, 'composite_to_stream');

export const PHANTOM_BUS_NAME = 'org.gnome.Phantom';
export const PHANTOM_OBJECT_PATH = '/org/gnome/Phantom';

export const UUID = 'phantom-ui@phantgnome';

export const SHELL_SCHEMA = 'org.gnome.shell';
export const DISABLED_EXTENSIONS_KEY = 'disabled-extensions';
export const VERSION_VALIDATION_KEY = 'disable-extension-version-validation';

export const VALID_STAGES = ['A', 'B', 'C'];

export const BTN_LEFT = 1;
export const BTN_STATE_PRESSED = (Clutter.ButtonState && Clutter.ButtonState.PRESSED) || 1;
export const BTN_STATE_RELEASED = (Clutter.ButtonState && Clutter.ButtonState.RELEASED) || 0;
