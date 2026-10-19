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

    add Solid("#000000")

    vbox:
        xalign 0.5
        yalign 0.5
        spacing 30

        text "@@ART_STYLE_TITLE@@" xalign 0.5 size 60
        text "@@ART_STYLE_HINT@@" xalign 0.5 size 28

        null height 20

        for style_id, style_label in vn_art_styles:
            textbutton style_label:
                xalign 0.5
                action [SetField(persistent, "art_style", style_id),
                    SetField(persistent, "art_style_chosen", True), Return()]

## Shown once, before the first scene, when there is a choice to make.
label vn_pick_art_style:
    if len(vn_art_styles) > 1 and not persistent.art_style_chosen:
        scene black
        call screen vn_art_style_picker
    return
