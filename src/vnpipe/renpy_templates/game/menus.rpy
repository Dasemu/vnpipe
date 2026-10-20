## Scene select and credits screens, reached from the main menu. The data they show
## (`vn_scenes`, `vn_credits`) is generated in menu_data.rpy; static otherwise.

## Labels of the scenes the player has reached; updated when a scene starts.
default persistent.vn_seen_scenes = set()

## The scene being played (a label of `vn_scenes`); saved with the game.
default vn_current_scene = None

init python:
    def vn_mark_scene(label):
        store.vn_current_scene = label
        if label not in persistent.vn_seen_scenes:
            persistent.vn_seen_scenes.add(label)
            renpy.save_persistent()

    ## Save metadata shown by the file slots (screens.rpy): the scene title and the first
    ## line of the text on screen. Text tags are dropped and markup is escaped.
    VN_EXCERPT_CHARS = 90

    def vn_excerpt(what):
        line = renpy.filter_text_tags(what or "", allow=[]).strip().split("\n")[0].strip()
        if len(line) > VN_EXCERPT_CHARS:
            line = line[:VN_EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
        return line.replace("[", "[[").replace("{", "{{")

    def vn_save_json(d):
        titles = dict((l, t) for l, t in vn_scenes)
        if store.vn_current_scene in titles:
            d["vn_scene"] = titles[store.vn_current_scene]
        if _history_list:
            last = _history_list[-1]
            d["vn_who"] = (last.who or "").replace("[", "[[").replace("{", "{{")
            d["vn_line"] = vn_excerpt(last.what)

    config.save_json_callbacks.append(vn_save_json)

screen vn_scene_select():

    tag menu

    use game_menu("@@UI_SCENES@@", scroll="viewport"):

        style_prefix "vn_scenes"

        vbox:
            spacing 6

            for scene_label, scene_title in vn_scenes:
                if scene_label in persistent.vn_seen_scenes:
                    textbutton scene_title action Start(scene_label)
                else:
                    textbutton "@@LOCKED@@" action NullAction() sensitive False

style vn_scenes_button is gui_button
style vn_scenes_button_text is gui_button_text

style vn_scenes_button:
    properties gui.button_properties("navigation_button")
    xalign 0.0
    padding (26, 5, 20, 5)
    background Frame("gui/vn/nav_[prefix_]background.png", 6, 0)

style vn_scenes_button_text:
    properties gui.text_properties("navigation_button")
    xalign 0.0
    size 30

screen vn_credits():

    tag menu

    use game_menu("@@UI_CREDITS@@", scroll="viewport"):

        style_prefix "vn_credits"

        vbox:
            spacing 6

            for section_title, entries in vn_credits:
                null height 20
                label section_title
                for entry in entries:
                    text entry

style vn_credits_label is gui_label
style vn_credits_label_text is gui_label_text
style vn_credits_text is gui_text

style vn_credits_label_text:
    size gui.label_text_size

style vn_credits_text:
    size 26
    xmaximum 1100
