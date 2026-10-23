## Art styles: the player picks one at first launch and can change it in Preferences.
## Every image is a ConditionSwitch on `persistent.art_style` (see images.rpy), so a
## change takes effect on the next interaction without moving anything on stage.
## Static except for the substituted style list; copied by `vnpipe renpy`.

default persistent.art_style = "@@DEFAULT_STYLE@@"
default persistent.art_style_chosen = False

## (style id, label) pairs, in manifest order.
define vn_art_styles = @@ART_STYLES@@

screen vn_art_style_picker():

    modal True

    add gui.main_menu_background
    add Solid("#000000c8")

    vbox:
        xalign 0.5
        yalign 0.45
        spacing 24

        text "@@ART_STYLE_TITLE@@":
            xalign 0.5
            size gui.title_text_size
            color gui.accent_color

        text "@@ART_STYLE_HINT@@":
            xalign 0.5
            size 30
            italic True

        null height 40

        if len(vn_languages) > 1:
            hbox:
                xalign 0.5
                spacing 40
                for lang, lang_label in vn_languages:
                    textbutton lang_label:
                        style_prefix "vn_language"
                        action [Function(vn_set_language, lang), Language(lang)]

            null height 20

        for style_id, style_label in vn_art_styles:
            textbutton style_label:
                style_prefix "vn_picker"
                xalign 0.5
                action [SetField(persistent, "art_style", style_id),
                    SetField(persistent, "art_style_chosen", True), Return()]

style vn_picker_button is gui_button
style vn_picker_button_text is gui_button_text

style vn_picker_button:
    xminimum 420
    padding (30, 12)

style vn_picker_button_text:
    size 44
    xalign 0.5
    text_align 0.5

style vn_language_button is gui_button
style vn_language_button_text is gui_button_text

style vn_language_button:
    padding (16, 6)

style vn_language_button_text:
    size 32
    selected_color gui.accent_color
    selected_underline True

## Shown once, before the first scene, when there is a choice to make (an art style, and
## the language when the game has translations).
label vn_pick_art_style:
    if (len(vn_art_styles) > 1 or len(vn_languages) > 1) and not persistent.art_style_chosen:
        scene black
        $ quick_menu = False
        call screen vn_art_style_picker
        $ quick_menu = True
    return
