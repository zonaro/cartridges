# dialog_backdrop.py
#
# Copyright 2026 joaomgabaldi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep a dialog from moving the window it is shown in, or from acting as its title bar.

libadwaita builds both the dimmed area around a dialog and the dialog's own
bars (its title bar, and whatever else sits above or below its content) out of
`GtkWindowHandle`, the same widget as the main window's title bar. Dragging any
of them moves the whole window, double-clicking one maximizes it, and
right-clicking one opens the window's menu — whose Close shuts the whole app,
not the dialog. On Windows a window is moved by its own title bar and by
nothing else, and a dialog drawn inside the window is not that title bar.

The handles are private and are rebuilt whenever the dialog switches between
floating and bottom sheet, so their gestures cannot simply be removed once and
forgotten. Instead the dialog itself — which outlives every sheet it is shown
in — takes gestures in the capture phase, ahead of the handles' own in the
bubble phase, and claims the press before a handle can act on it:

- A press on the dimmed area, the one handle with nothing inside, is claimed
  outright.
- A drag starting on a bar is claimed once it travels as far as the handle
  waits for before moving the window. A press alone is left alone, since the
  bar's buttons only take it when released.
- A double-click is not claimed at all: a bar maximizes through the
  `window.toggle-maximized` action, which the dialog answers itself with an
  action that does nothing. Buttons and search boxes in the bars keep their
  own double-clicks.
- A right-click on a bar or on the dimmed area is claimed outright, except on
  a search box in a bar, which takes it for its own copy-and-paste menu.

The bottom sheet used in a narrow window dims with a plain gizmo rather than a
window handle and closes when tapped; nothing here touches that.
"""

from gi.repository import Adw, Gdk, Gio, Gtk

_BLOCKED = "_cartridges_backdrop_drag_blocked"


def block_window_drag(dialog: Adw.Dialog) -> None:
    """Stop presses in `dialog` and around it from acting on the window."""
    # A dialog becomes the visible one again every time a dialog stacked on top
    # of it closes, and one set of gestures is enough.
    if getattr(dialog, _BLOCKED, False):
        return
    setattr(dialog, _BLOCKED, True)

    click = Gtk.GestureClick(
        button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE
    )
    click.connect("pressed", _claim_backdrop_press)
    dialog.add_controller(click)

    # The window's menu is not an action the dialog could answer in its place:
    # the handle asks Windows for it directly.
    menu = Gtk.GestureClick(
        button=Gdk.BUTTON_SECONDARY, propagation_phase=Gtk.PropagationPhase.CAPTURE
    )
    menu.connect("pressed", _claim_menu_press)
    dialog.add_controller(menu)

    drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    drag.connect("drag-update", _claim_bar_drag)
    dialog.add_controller(drag)

    # Looked up from the handle upwards, so this one is found before the main
    # window's. Every other `window.` action still reaches the window.
    actions = Gio.SimpleActionGroup()
    actions.add_action(Gio.SimpleAction(name="toggle-maximized"))
    dialog.insert_action_group("window", actions)


def drag_would_move_window(
    dialog: Adw.Dialog, target: Gtk.Widget | None, offset_x: float, offset_y: float
) -> bool:
    """Whether a drag from `target`, this far along, would move the window.

    The handle starts moving once the drag passes the drag threshold; this
    answers yes on reaching it, so on the motion where both would act, the
    dialog's gesture — run first — gets there before the handle does.
    """
    threshold = dialog.get_settings().props.gtk_dnd_drag_threshold
    if max(abs(offset_x), abs(offset_y)) < threshold:
        return False

    return reaches_window_handle(dialog, target)


def reaches_window_handle(dialog: Adw.Dialog, target: Gtk.Widget | None) -> bool:
    """Whether a press on `target` is left for a window handle to act on.

    A text box takes every press on it for itself, the right-click included, so
    one sitting in a bar keeps them.
    """
    while target is not None and target is not dialog:
        if isinstance(target, Gtk.Editable):
            return False
        if isinstance(target, Gtk.WindowHandle):
            return True
        target = target.get_parent()
    return False


def _claim_backdrop_press(
    gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
) -> None:
    """Claim the press when it landed on the backdrop, and only then.

    The backdrop is the one window handle in a dialog with nothing inside it:
    the handles wrapping a bar all hold the bar's own box.
    """
    target = gesture.get_widget().pick(x, y, Gtk.PickFlags.DEFAULT)

    if isinstance(target, Gtk.WindowHandle) and target.get_child() is None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)


def _claim_menu_press(
    gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
) -> None:
    dialog = gesture.get_widget()

    if reaches_window_handle(dialog, dialog.pick(x, y, Gtk.PickFlags.DEFAULT)):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)


def _claim_bar_drag(gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
    dialog = gesture.get_widget()
    _found, x, y = gesture.get_start_point()
    target = dialog.pick(x, y, Gtk.PickFlags.DEFAULT)

    if drag_would_move_window(dialog, target, offset_x, offset_y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
