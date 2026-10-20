################################################################################
## Initialization
################################################################################

init offset = -1


################################################################################
## Styles
################################################################################

style default:
    properties gui.text_properties()
    language gui.language

style input:
    properties gui.text_properties("input", accent=True)
    adjust_spacing False

style hyperlink_text:
    properties gui.text_properties("hyperlink", accent=True)
    hover_underline True

style gui_text:
    properties gui.text_properties("interface")


style button:
    properties gui.button_properties("button")

style button_text is gui_text:
    properties gui.text_properties("button")
    yalign 0.5


style label_text is gui_text:
    properties gui.text_properties("label", accent=True)

style prompt_text is gui_text:
    properties gui.text_properties("prompt")


style bar:
    ysize gui.bar_size
    left_bar Frame("gui/bar/left.png", gui.bar_borders, tile=gui.bar_tile)
    right_bar Frame("gui/bar/right.png", gui.bar_borders, tile=gui.bar_tile)

style vbar:
    xsize gui.bar_size
    top_bar Frame("gui/bar/top.png", gui.vbar_borders, tile=gui.bar_tile)
    bottom_bar Frame("gui/bar/bottom.png", gui.vbar_borders, tile=gui.bar_tile)

style scrollbar:
    ysize gui.scrollbar_size
    base_bar Frame("gui/scrollbar/horizontal_[prefix_]bar.png", gui.scrollbar_borders, tile=gui.scrollbar_tile)
    thumb Frame("gui/scrollbar/horizontal_[prefix_]thumb.png", gui.scrollbar_borders, tile=gui.scrollbar_tile)

style vscrollbar:
    xsize gui.scrollbar_size
    base_bar Frame("gui/scrollbar/vertical_[prefix_]bar.png", gui.vscrollbar_borders, tile=gui.scrollbar_tile)
    thumb Frame("gui/scrollbar/vertical_[prefix_]thumb.png", gui.vscrollbar_borders, tile=gui.scrollbar_tile)

style slider:
    ysize gui.slider_size
    base_bar Frame("gui/slider/horizontal_[prefix_]bar.png", gui.slider_borders, tile=gui.slider_tile)
    thumb "gui/slider/horizontal_[prefix_]thumb.png"

style vslider:
    xsize gui.slider_size
    base_bar Frame("gui/slider/vertical_[prefix_]bar.png", gui.vslider_borders, tile=gui.slider_tile)
    thumb "gui/slider/vertical_[prefix_]thumb.png"


style frame:
    padding gui.frame_borders.padding
    background Frame("gui/frame.png", gui.frame_borders, tile=gui.frame_tile)



################################################################################
## In-game screens
################################################################################


## Say screen ##################################################################
##
## The say screen is used to display dialogue to the player. It takes two
## parameters, who and what, which are the name of the speaking character and
## the text to be displayed, respectively. (The who parameter can be None if no
## name is given.)
##
## This screen must create a text displayable with id "what", as Ren'Py uses
## this to manage text display. It can also create displayables with id "who"
## and id "window" to apply style properties.
##
## https://www.renpy.org/doc/html/screen_special.html#say

screen say(who, what):

    window:
        id "window"

        if who is not None:

            window:
                id "namebox"
                style "namebox"
                text who id "who"

        text what id "what"


    ## If there's a side image, display it above the text. Do not display on the
    ## phone variant - there's no room.
    if not renpy.variant("small"):
        add SideImage() xalign 0.0 yalign 1.0


## Make the namebox available for styling through the Character object.
init python:
    config.character_id_prefixes.append('namebox')

style window is default
style say_label is default
style say_dialogue is default
style say_thought is say_dialogue

style namebox is default
style namebox_label is say_label


style window:
    xalign 0.5
    xfill True
    yalign gui.textbox_yalign
    ysize gui.textbox_height

    background Frame("gui/textbox.png", 0, 0)

style namebox:
    xpos gui.name_xpos
    xanchor gui.name_xalign
    xsize gui.namebox_width
    ypos gui.name_ypos
    ysize gui.namebox_height

    background Frame("gui/namebox.png", gui.namebox_borders, tile=gui.namebox_tile, xalign=gui.name_xalign)
    padding gui.namebox_borders.padding

style say_label:
    properties gui.text_properties("name", accent=True)
    xalign gui.name_xalign
    yalign 0.5

style say_dialogue:
    properties gui.text_properties("dialogue")

    xpos gui.dialogue_xpos
    xsize gui.dialogue_width
    ypos gui.dialogue_ypos

    adjust_spacing False

## Input screen ################################################################
##
## This screen is used to display renpy.input. The prompt parameter is used to
## pass a text prompt in.
##
## This screen must create an input displayable with id "input" to accept the
## various input parameters.
##
## https://www.renpy.org/doc/html/screen_special.html#input

screen input(prompt):
    style_prefix "input"

    window:

        vbox:
            xanchor gui.dialogue_text_xalign
            xpos gui.dialogue_xpos
            xsize gui.dialogue_width
            ypos gui.dialogue_ypos

            text prompt style "input_prompt"
            input id "input"

style input_prompt is default

style input_prompt:
    xalign gui.dialogue_text_xalign
    properties gui.text_properties("input_prompt")

style input:
    xalign gui.dialogue_text_xalign
    xmaximum gui.dialogue_width


## Choice screen ###############################################################
##
## This screen is used to display the in-game choices presented by the menu
## statement. The one parameter, items, is a list of objects, each with caption
## and action fields.
##
## https://www.renpy.org/doc/html/screen_special.html#choice

screen choice(items):
    style_prefix "choice"

    vbox:
        for i in items:
            textbutton i.caption action i.action


style choice_vbox is vbox
style choice_button is button
style choice_button_text is button_text

style choice_vbox:
    xalign 0.5
    ypos 405
    yanchor 0.5

    spacing gui.choice_spacing

style choice_button is default:
    properties gui.button_properties("choice_button")

style choice_button_text is default:
    properties gui.text_properties("choice_button")



## Quick Menu screen ###########################################################
##
## The quick menu is displayed in-game to provide easy access to the out-of-game
## menus. It sits on its own dark strip so it stays legible over any textbox.

screen quick_menu():

    ## Ensure this appears on top of other screens.
    zorder 100

    if quick_menu:

        frame:
            style "quick_frame"

            hbox:
                style_prefix "quick"
                style "quick_menu"

                textbutton _("Back") action Rollback()
                textbutton _("History") action ShowMenu('history')
                textbutton _("Skip") action Skip() alternate Skip(fast=True, confirm=True)
                textbutton _("Auto") action Preference("auto-forward", "toggle")
                textbutton _("Save") action ShowMenu('save')
                textbutton _("Q.Save") action QuickSave()
                textbutton _("Q.Load") action QuickLoad()
                textbutton _("Prefs") action ShowMenu('preferences')


## This code ensures that the quick_menu screen is displayed in-game, whenever
## the player has not explicitly hidden the interface.
init python:
    config.overlay_screens.append("quick_menu")

default quick_menu = True

style quick_frame is empty
style quick_menu is hbox
style quick_button is default
style quick_button_text is button_text

style quick_frame:
    xalign 0.5
    yalign 1.0
    padding (24, 2, 24, 2)
    background Solid("#0b0907b8")

style quick_menu:
    spacing 4

style quick_button:
    properties gui.button_properties("quick_button")

style quick_button_text:
    properties gui.text_properties("quick_button")
    kerning 1


################################################################################
## Shared menu ornaments
################################################################################

## A gold rule with a diamond at its centre (as on the scene title cards).
image vn_rule = "gui/vn/rule.png"
image vn_rule_small = "gui/vn/rule_small.png"

## Darkens a menu background towards the edges so the panels stay readable.
image vn_vignette = Transform("gui/vn/vignette.png", xysize=(config.screen_width, config.screen_height))

## A diamond glyph, from a font that has it.
define vn_diamond = "{font=DejaVuSans.ttf}◆{/font}"


################################################################################
## Main and Game Menu Screens
################################################################################

## Navigation screen ###########################################################
##
## This screen is included in the main and game menus, and provides navigation
## to other menus, and to start the game. On the main menu it is a centred list
## under the title; in the game menus, a column on the left.

screen navigation(centered=False):

    vbox:
        style_prefix ("main_nav" if centered else "navigation")

        if main_menu:

            textbutton _("Start") action Start()

            textbutton "@@UI_SCENES@@" action ShowMenu("vn_scene_select")

        else:

            textbutton _("History") action ShowMenu("history")

            textbutton _("Save") action ShowMenu("save")

        textbutton _("Load") action ShowMenu("load")

        textbutton _("Preferences") action ShowMenu("preferences")

        if _in_replay:

            textbutton _("End Replay") action EndReplay(confirm=True)

        elif not main_menu:

            textbutton _("Main Menu") action MainMenu()

        textbutton _("About") action ShowMenu("about")

        textbutton "@@UI_CREDITS@@" action ShowMenu("vn_credits")

        if renpy.variant("pc") or (renpy.variant("web") and not renpy.variant("mobile")):

            ## Help isn't necessary or relevant to mobile devices.
            textbutton _("Help") action ShowMenu("help")

        if renpy.variant("pc"):

            ## The quit button is banned on iOS and unnecessary on Android and
            ## Web.
            textbutton _("Quit") action Quit(confirm=not main_menu)


style navigation_vbox is vbox
style navigation_button is gui_button
style navigation_button_text is gui_button_text

style navigation_vbox:
    xpos gui.navigation_xpos
    yalign 0.5
    spacing gui.navigation_spacing

style navigation_button:
    size_group "navigation"
    properties gui.button_properties("navigation_button")
    xminimum 300
    padding (26, 9, 20, 9)
    background Frame("gui/vn/nav_[prefix_]background.png", 6, 0)

style navigation_button_text:
    properties gui.text_properties("navigation_button")
    size 31
    kerning 1

style main_nav_vbox is vbox
style main_nav_button is gui_button
style main_nav_button_text is gui_button_text

style main_nav_vbox:
    xalign 0.5
    ypos 520
    spacing 2

style main_nav_button:
    xalign 0.5
    padding (40, 4)

style main_nav_button_text:
    properties gui.text_properties("navigation_button")
    size 34
    kerning 3
    xalign 0.5
    textalign 0.5
    outlines [(2, "#000000a0", 0, 1)]


## Main Menu screen ############################################################
##
## Used to display the main menu when Ren'Py starts: the title as on a book's
## title page, a gold rule, and the menu centred below.
##
## https://www.renpy.org/doc/html/screen_special.html#main-menu

screen main_menu():

    ## This ensures that any other menu screen is replaced.
    tag menu

    add gui.main_menu_background
    add "vn_vignette"

    if gui.show_name:

        vbox:
            style "main_menu_vbox"

            text "[config.name!t]":
                style "main_menu_title"

            add "vn_rule" xalign 0.5

            if vn_subtitle:
                text "[vn_subtitle!t]":
                    style "main_menu_subtitle"

    ## The actual contents of the main menu are in the navigation screen.
    use navigation(centered=True)

    text "[config.version]":
        style "main_menu_version"


style main_menu_vbox is vbox
style main_menu_text is gui_text
style main_menu_title is main_menu_text
style main_menu_subtitle is main_menu_text
style main_menu_version is main_menu_text

style main_menu_vbox:
    xalign 0.5
    ypos 150
    spacing 22

style main_menu_text:
    properties gui.text_properties("main_menu", accent=True)

style main_menu_title:
    properties gui.text_properties("title")
    size 132
    kerning 14
    xalign 0.5
    textalign 0.5
    color "#efe6d2"
    outlines [(4, "#000000b0", 0, 3)]

style main_menu_subtitle:
    size 44
    italic True
    kerning 3
    xalign 0.5
    textalign 0.5
    color "#c9a25b"
    outlines [(3, "#000000b0", 0, 2)]

style main_menu_version:
    properties gui.text_properties("version")
    size 20
    color "#efe6d260"
    xalign 1.0
    yalign 1.0
    xoffset -30
    yoffset -20


## Game Menu screen ############################################################
##
## This lays out the basic common structure of a game menu screen: the art
## behind a dark shade, a navigation column on the left with the work's title,
## and the screen title over the content, underlined by the gold rule.
##
## The scroll parameter can be None, or one of "viewport" or "vpgrid".
## This screen is intended to be used with one or more children, which are
## transcluded (placed) inside it.

screen game_menu(title, scroll=None, yinitial=0.0, spacing=0):

    style_prefix "game_menu"

    if main_menu:
        add gui.main_menu_background
    else:
        add gui.game_menu_background

    add "vn_vignette"
    add Solid("#000000a0")

    ## The navigation column.
    frame:
        style "game_menu_navigation_frame"

    text "[config.name!t]":
        style "game_menu_work_title"

    frame:
        style "game_menu_work_rule"
        add "vn_rule_small"

    ## The title and its rule, centred over the content.
    vbox:
        style "game_menu_title_vbox"

        label title

        add "vn_rule" xalign 0.5

    frame:
        style "game_menu_content_frame"

        if scroll == "viewport":

            viewport:
                yinitial yinitial
                scrollbars "vertical"
                mousewheel True
                draggable True
                pagekeys True

                side_yfill True

                vbox:
                    spacing spacing

                    transclude

        elif scroll == "vpgrid":

            vpgrid:
                cols 1
                yinitial yinitial

                scrollbars "vertical"
                mousewheel True
                draggable True
                pagekeys True

                side_yfill True

                spacing spacing

                transclude

        else:

            transclude

    use navigation

    textbutton _("Return"):
        style "return_button"

        action Return()

    if main_menu:
        key "game_menu" action ShowMenu("main_menu")


style game_menu_navigation_frame is empty
style game_menu_content_frame is empty
style game_menu_title_vbox is vbox
style game_menu_work_title is gui_text
style game_menu_work_rule is empty
style game_menu_viewport is gui_viewport
style game_menu_side is gui_side
style game_menu_scrollbar is gui_vscrollbar

style game_menu_label is gui_label
style game_menu_label_text is gui_label_text

style return_button is navigation_button
style return_button_text is navigation_button_text

style game_menu_navigation_frame:
    xsize 400
    yfill True
    background Fixed(Solid("#0b0907d8"), Solid("#c9a25b50", xsize=1, ysize=960, xpos=399, ypos=60))

style game_menu_work_title:
    xpos 200
    xanchor 0.5
    xmaximum 360
    textalign 0.5
    ypos 62
    size 34
    kerning 8
    color gui.accent_color

style game_menu_work_rule:
    xpos 200
    xanchor 0.5
    ypos 122

style game_menu_title_vbox:
    xpos 1160
    xanchor 0.5
    ypos 44
    spacing 4

style game_menu_content_frame:
    xpos 480
    ypos 228
    xsize 1360
    ysize 800

style game_menu_viewport:
    xsize 1360

style game_menu_vscrollbar:
    unscrollable gui.unscrollable

style game_menu_side:
    spacing 15

style game_menu_label:
    xalign 0.5

style game_menu_label_text:
    size 76
    kerning 3
    color "#efe6d2"
    xalign 0.5
    outlines [(3, "#00000090", 0, 2)]

style return_button:
    xpos gui.navigation_xpos
    yalign 1.0
    yoffset -45


## About screen ################################################################
##
## This screen gives credit and copyright information about the game and Ren'Py.

screen about():

    tag menu

    use game_menu(_("About"), scroll="viewport"):

        style_prefix "about"

        vbox:
            xsize 1300
            spacing 6

            label "[config.name!t]"
            text _("Version [config.version!t]\n") style "about_version"

            ## gui.about is usually set in options.rpy.
            if gui.about:
                text "[gui.about!t]\n"

            add "vn_rule_small"
            null height 16

            text _("Made with {a=https://www.renpy.org/}Ren'Py{/a} [renpy.version_only].\n\n[renpy.license!t]"):
                style "about_small"


style about_label is gui_label
style about_label_text is gui_label_text
style about_text is gui_text
style about_version is about_text
style about_small is about_text

style about_label_text:
    size 48
    kerning 4
    color gui.accent_color

style about_text:
    size 30

style about_version:
    size 24
    italic True
    color "#efe6d2a0"

style about_small:
    size 22
    color "#efe6d2b0"


## Load and Save screens #######################################################
##
## Both screens share file_slots: a page of saves shown as entries of a book's
## register (thumbnail, scene title, the line that was on screen and the date),
## a page name the player can edit, and pages numbered in Roman numerals. The
## load screen adds the autosave and quick save pages.
##
## https://www.renpy.org/doc/html/screen_special.html#save https://
## www.renpy.org/doc/html/screen_special.html#load

screen save():

    tag menu

    ## The automatic and quick save pages are only offered when loading.
    on "show" action If(FileCurrentPage() in ("auto", "quick"), FilePage(1))
    on "replace" action If(FileCurrentPage() in ("auto", "quick"), FilePage(1))

    use file_slots(_("Save"))


screen load():

    tag menu

    use file_slots(_("Load"))


init python:

    def vn_roman(n):
        """1 -> I, 4 -> IV, 9 -> IX, ..."""
        out = ""
        for value, numeral in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
                (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"),
                (1, "I")):
            while n >= value:
                out += numeral
                n -= value
        return out

    class VnPageNameInputValue(FilePageNameInputValue):
        """The editable page name, whose default uses the Roman page number."""

        def _default(self, page):
            return __(self.pattern).format(vn_roman(int(page)))

        def get_text(self):
            page = self.get_page()
            if page in ("auto", "quick"):
                return FilePageNameInputValue.get_text(self)
            default = self._default(page)
            rv = persistent._file_page_name.get(int(page), default)
            if not rv.strip():
                current, active = renpy.get_editable_input_value()
                if not ((current is self) and active):
                    rv = default
            return rv

        def set_text(self, s):
            page = self.get_page()
            if page in ("auto", "quick"):
                return
            if s == self._default(page):
                persistent._file_page_name.pop(int(page), None)
            else:
                persistent._file_page_name[int(page)] = s


screen file_slots(title):

    default page_name_value = VnPageNameInputValue(pattern=_("Page {}"), auto=_("Automatic saves"), quick=_("Quick saves"))

    $ loading = title != _("Save")

    use game_menu(title):

        fixed:

            ## This ensures the input will get the enter event before any of the
            ## buttons do.
            order_reverse True

            ## The page name, which can be edited by clicking on it.
            button:
                style "page_label"

                key_events True
                xalign 0.5
                action page_name_value.Toggle()

                input:
                    style "page_label_text"
                    value page_name_value

            ## The slots of this page, one entry per row.
            grid gui.file_slot_cols gui.file_slot_rows:
                style_prefix "slot"

                xalign 0.5
                ypos 70

                spacing gui.slot_spacing

                for i in range(gui.file_slot_cols * gui.file_slot_rows):

                    $ slot = i + 1
                    $ info = FileJson(slot)

                    fixed:
                        style "slot_fixed"

                        button:
                            action FileAction(slot)
                            if info is None:
                                style "slot_empty_button"

                            hbox:
                                spacing 34
                                yalign 0.5

                                frame:
                                    style "slot_thumb_frame"
                                    if info is None:
                                        frame:
                                            style "slot_thumb_blank"
                                            text vn_diamond style "slot_thumb_empty"
                                    else:
                                        add Transform(FileScreenshot(slot), xysize=gui.vn_slot_thumb)

                                vbox:
                                    yalign 0.5
                                    spacing 6

                                    if info is None:
                                        text _("empty slot") style "slot_empty_text"
                                    else:
                                        text (info.get("vn_scene") or FileSaveName(slot) or _("Saved game")):
                                            style "slot_scene_text"
                                        if info.get("vn_line"):
                                            hbox:
                                                spacing 14
                                                if info.get("vn_who"):
                                                    text info["vn_who"] style "slot_who_text"
                                                text ("«" + info["vn_line"] + "»") style "slot_line_text"
                                        text FileTime(slot, format=_("{#vn_file_time}%A, %d/%m/%Y · %H:%M")):
                                            style "slot_time_text"

                            key "save_delete" action FileDelete(slot)

                        if info is not None:
                            textbutton _("Delete"):
                                style "slot_delete_button"
                                action FileDelete(slot)

            ## Buttons to access other pages.
            hbox:
                style_prefix "page"

                xalign 0.5
                yalign 1.0
                yoffset -6
                spacing gui.page_spacing

                textbutton "‹" action FilePagePrevious(auto=loading, quick=loading) text_style "page_arrow_text"
                key "save_page_prev" action FilePagePrevious(auto=loading, quick=loading)

                ## Saving into the automatic or quick slots makes no sense, so
                ## those pages are only offered when loading.
                if loading:
                    if config.has_autosave:
                        textbutton _("Autosaves") action FilePage("auto")

                    if config.has_quicksave:
                        textbutton _("Quick saves") action FilePage("quick")

                    text vn_diamond style "page_separator"

                ## range(1, 10) gives the numbers from 1 to 9.
                for page in range(1, 10):
                    textbutton vn_roman(page) action FilePage(page)

                textbutton "›" action FilePageNext(auto=loading, quick=loading) text_style "page_arrow_text"
                key "save_page_next" action FilePageNext(auto=loading, quick=loading)


## Size of the slot thumbnails (config.thumbnail_* is the saved size).
define gui.vn_slot_thumb = (256, 144)

style page_label is gui_label
style page_label_text is gui_label_text
style page_button is gui_button
style page_button_text is gui_button_text
style page_arrow_text is page_button_text
style page_separator is gui_text

style slot_fixed is fixed
style slot_button is gui_button
style slot_empty_button is slot_button
style slot_button_text is gui_button_text
style slot_thumb_frame is empty
style slot_thumb_blank is empty
style slot_thumb_empty is gui_text
style slot_scene_text is gui_text
style slot_who_text is gui_text
style slot_line_text is gui_text
style slot_time_text is gui_text
style slot_empty_text is gui_text
style slot_delete_button is gui_button
style slot_delete_button_text is gui_button_text

style page_label:
    xpadding 60
    ypadding 4
    xalign 0.5

style page_label_text:
    textalign 0.5
    layout "subtitle"
    size 32
    italic True
    kerning 2
    color gui.accent_color
    hover_color gui.hover_color

style page_button:
    properties gui.button_properties("page_button")
    yalign 0.5

style page_button_text:
    properties gui.text_properties("page_button")
    size 28
    kerning 2

style page_arrow_text:
    font "DejaVuSans.ttf"
    size 40

style page_separator:
    size 16
    color "#c9a25b90"
    yalign 0.5
    xmargin 18

style slot_fixed:
    xsize 1360
    ysize 156

style slot_button:
    xfill True
    yfill True
    padding (18, 10, 18, 10)
    background Frame("gui/vn/card_[prefix_]background.png", 12, 12)

style slot_empty_button:
    background Frame("gui/vn/card_empty.png", 12, 12)
    hover_background Frame("gui/vn/card_hover_background.png", 12, 12)

style slot_thumb_frame:
    padding (2, 2)
    background Solid("#c9a25b70")
    yalign 0.5

style slot_thumb_blank:
    xysize gui.vn_slot_thumb
    background Solid("#0b0907")

style slot_thumb_empty:
    align (0.5, 0.5)
    size 22
    color "#c9a25b60"

style slot_scene_text:
    size 30
    kerning 2
    color gui.accent_color
    hover_color gui.hover_color
    xmaximum 860

style slot_who_text:
    size 23
    color "#efe6d290"

style slot_line_text:
    size 24
    italic True
    color "#efe6d2"
    xmaximum 760

style slot_time_text:
    size 21
    color "#efe6d290"

style slot_empty_text:
    size 26
    italic True
    color "#efe6d260"
    hover_color gui.hover_color

style slot_delete_button:
    xalign 1.0
    yalign 0.5
    xoffset -28
    padding (14, 6)

style slot_delete_button_text:
    size 22
    kerning 1
    idle_color "#efe6d2a8"
    hover_color "#e6866f"


## Preferences screen ##########################################################
##
## The preferences screen allows the player to configure the game to better suit
## themselves. Each group of choices sits on its own framed panel.
##
## https://www.renpy.org/doc/html/screen_special.html#preferences

screen preferences():

    tag menu

    use game_menu(_("Preferences"), scroll="viewport"):

        vbox:
            spacing 30

            hbox:
                box_wrap True
                spacing 30
                box_wrap_spacing 30

                if renpy.variant("pc") or renpy.variant("web"):

                    frame:
                        style "pref_panel"
                        vbox:
                            style_prefix "radio"
                            label _("Display")
                            textbutton _("Window") action Preference("display", "window")
                            textbutton _("Fullscreen") action Preference("display", "fullscreen")

                frame:
                    style "pref_panel"
                    vbox:
                        style_prefix "check"
                        label _("Skip")
                        textbutton _("Unseen Text") action Preference("skip", "toggle")
                        textbutton _("After Choices") action Preference("after choices", "toggle")
                        textbutton _("Transitions") action InvertSelected(Preference("transitions", "toggle"))

                if len(vn_art_styles) > 1:

                    frame:
                        style "pref_panel"
                        vbox:
                            style_prefix "radio"
                            label "@@ART_STYLE_TITLE@@"
                            for style_id, style_label in vn_art_styles:
                                textbutton style_label action SetField(persistent, "art_style", style_id)

                if len(vn_languages) > 1:

                    frame:
                        style "pref_panel"
                        vbox:
                            style_prefix "radio"
                            label _("Language")
                            ## Each language under its own name, so it can be found in any.
                            for lang, lang_label in vn_languages:
                                textbutton lang_label action [Function(vn_set_language, lang), Language(lang)]

                ## Additional panels of type "radio_pref" or "check_pref" can be
                ## added here, to add additional creator-defined preferences.

            hbox:
                style_prefix "slider"
                box_wrap True
                spacing 30
                box_wrap_spacing 30

                frame:
                    style "pref_panel"
                    vbox:

                        label _("Text Speed")

                        bar value Preference("text speed")

                        label _("Auto-Forward Time")

                        bar value Preference("auto-forward time")

                frame:
                    style "pref_panel"
                    vbox:

                        if config.has_music:
                            label _("Music Volume")

                            hbox:
                                bar value Preference("music volume")

                        if config.has_sound:

                            label _("Sound Volume")

                            hbox:
                                bar value Preference("sound volume")

                                if config.sample_sound:
                                    textbutton _("Test") action Play("sound", config.sample_sound)


                        if config.has_voice:
                            label _("Voice Volume")

                            hbox:
                                bar value Preference("voice volume")

                                if config.sample_voice:
                                    textbutton _("Test") action Play("voice", config.sample_voice)

                        if config.has_music or config.has_sound or config.has_voice:
                            null height gui.pref_spacing

                            textbutton _("Mute All"):
                                action Preference("all mute", "toggle")
                                style "mute_all_button"


style pref_panel is empty
style pref_label is gui_label
style pref_label_text is gui_label_text
style pref_vbox is vbox

style radio_label is pref_label
style radio_label_text is pref_label_text
style radio_button is gui_button
style radio_button_text is gui_button_text
style radio_vbox is pref_vbox

style check_label is pref_label
style check_label_text is pref_label_text
style check_button is gui_button
style check_button_text is gui_button_text
style check_vbox is pref_vbox

style slider_label is pref_label
style slider_label_text is pref_label_text
style slider_slider is gui_slider
style slider_button is gui_button
style slider_button_text is gui_button_text
style slider_pref_vbox is pref_vbox

style mute_all_button is check_button
style mute_all_button_text is check_button_text

style pref_panel:
    background Frame("gui/vn/card_background.png", 12, 12)
    padding (30, 22, 30, 28)

style pref_label:
    top_margin gui.pref_spacing
    bottom_margin 8

style pref_label_text:
    yalign 1.0
    size 28
    kerning 2
    color gui.accent_color

style pref_vbox:
    xsize 370

style radio_vbox:
    spacing gui.pref_button_spacing

style radio_button:
    properties gui.button_properties("radio_button")
    foreground "gui/button/radio_[prefix_]foreground.png"

style radio_button_text:
    properties gui.text_properties("radio_button")

style check_vbox:
    spacing gui.pref_button_spacing

style check_button:
    properties gui.button_properties("check_button")
    foreground "gui/button/check_[prefix_]foreground.png"

style check_button_text:
    properties gui.text_properties("check_button")

style slider_slider:
    xsize 525

style slider_button:
    properties gui.button_properties("slider_button")
    yalign 0.5
    left_margin 15

style slider_button_text:
    properties gui.text_properties("slider_button")

style slider_vbox:
    xsize 580


## History screen ##############################################################
##
## This is a screen that displays the dialogue history to the player, each entry
## under a faint rule. It accesses the dialogue history in _history_list.
##
## https://www.renpy.org/doc/html/history.html

screen history():

    tag menu

    ## Avoid predicting this screen, as it can be very large.
    predict False

    use game_menu(_("History"), scroll=("vpgrid" if gui.history_height else "viewport"), yinitial=1.0, spacing=gui.history_spacing):

        style_prefix "history"

        for h in _history_list:

            window:

                ## This lays things out properly if history_height is None.
                has fixed:
                    yfit True

                if h.who:

                    label h.who:
                        style "history_name"
                        substitute False

                        ## Take the color of the who text from the Character, if
                        ## set.
                        if "color" in h.who_args:
                            text_color h.who_args["color"]

                $ what = renpy.filter_text_tags(h.what, allow=gui.history_allow_tags)
                text what:
                    substitute False

        if not _history_list:
            label _("The dialogue history is empty.")


## This determines what tags are allowed to be displayed on the history screen.

define gui.history_allow_tags = { "alt", "noalt", "rt", "rb", "art" }


style history_window is empty

style history_name is gui_label
style history_name_text is gui_label_text
style history_text is gui_text

style history_label is gui_label
style history_label_text is gui_label_text

style history_window:
    xfill True
    ysize gui.history_height
    top_padding 18
    bottom_padding 18
    background Frame("gui/vn/hairline.png", 0, 1, 0, 0)

style history_name:
    xpos gui.history_name_xpos
    xanchor gui.history_name_xalign
    ypos gui.history_name_ypos
    xsize gui.history_name_width

style history_name_text:
    min_width gui.history_name_width
    textalign gui.history_name_xalign
    size 28
    kerning 1

style history_text:
    xpos gui.history_text_xpos
    ypos gui.history_text_ypos
    xanchor gui.history_text_xalign
    xsize gui.history_text_width
    min_width gui.history_text_width
    textalign gui.history_text_xalign
    layout ("subtitle" if gui.history_text_xalign else "tex")
    size 28

style history_label:
    xfill True

style history_label_text:
    xalign 0.5
    italic True
    color "#efe6d2a0"


## Help screen #################################################################
##
## A screen that gives information about key and mouse bindings. It uses other
## screens (keyboard_help, mouse_help, and gamepad_help) to display the actual
## help.

screen help():

    tag menu

    default device = "keyboard"

    use game_menu(_("Help"), scroll="viewport"):

        style_prefix "help"

        vbox:
            spacing 16

            hbox:
                xalign 0.5
                spacing 20

                textbutton _("Keyboard") action SetScreenVariable("device", "keyboard")
                textbutton _("Mouse") action SetScreenVariable("device", "mouse")

                if GamepadExists():
                    textbutton _("Gamepad") action SetScreenVariable("device", "gamepad")

            null height 10

            if device == "keyboard":
                use keyboard_help
            elif device == "mouse":
                use mouse_help
            elif device == "gamepad":
                use gamepad_help


screen keyboard_help():

    hbox:
        label _("Enter")
        text _("Advances dialogue and activates the interface.")

    hbox:
        label _("Space")
        text _("Advances dialogue without selecting choices.")

    hbox:
        label _("Arrow Keys")
        text _("Navigate the interface.")

    hbox:
        label _("Escape")
        text _("Accesses the game menu.")

    hbox:
        label _("Ctrl")
        text _("Skips dialogue while held down.")

    hbox:
        label _("Tab")
        text _("Toggles dialogue skipping.")

    hbox:
        label _("Page Up")
        text _("Rolls back to earlier dialogue.")

    hbox:
        label _("Page Down")
        text _("Rolls forward to later dialogue.")

    hbox:
        label "H"
        text _("Hides the user interface.")

    hbox:
        label "S"
        text _("Takes a screenshot.")

    hbox:
        label "V"
        text _("Toggles assistive {a=https://www.renpy.org/l/voicing}self-voicing{/a}.")

    hbox:
        label "Shift+A"
        text _("Opens the accessibility menu.")


screen mouse_help():

    hbox:
        label _("Left Click")
        text _("Advances dialogue and activates the interface.")

    hbox:
        label _("Middle Click")
        text _("Hides the user interface.")

    hbox:
        label _("Right Click")
        text _("Accesses the game menu.")

    hbox:
        label _("Mouse Wheel Up")
        text _("Rolls back to earlier dialogue.")

    hbox:
        label _("Mouse Wheel Down")
        text _("Rolls forward to later dialogue.")


screen gamepad_help():

    hbox:
        label _("Right Trigger\nA/Bottom Button")
        text _("Advances dialogue and activates the interface.")

    hbox:
        label _("Left Trigger\nLeft Shoulder")
        text _("Rolls back to earlier dialogue.")

    hbox:
        label _("Right Shoulder")
        text _("Rolls forward to later dialogue.")

    hbox:
        label _("D-Pad, Sticks")
        text _("Navigate the interface.")

    hbox:
        label _("Start, Guide, B/Right Button")
        text _("Accesses the game menu.")

    hbox:
        label _("Y/Top Button")
        text _("Hides the user interface.")

    textbutton _("Calibrate") action GamepadCalibrate()


style help_button is gui_button
style help_button_text is gui_button_text
style help_label is gui_label
style help_label_text is gui_label_text
style help_text is gui_text

style help_button:
    properties gui.button_properties("help_button")
    padding (26, 8, 20, 8)
    background Frame("gui/vn/nav_[prefix_]background.png", 6, 0)

style help_button_text:
    properties gui.text_properties("help_button")
    size 30
    kerning 1

style help_label:
    xsize 420
    right_padding 36

style help_label_text:
    size 28
    color gui.accent_color
    xalign 1.0
    textalign 1.0

style help_text:
    size 28
    xmaximum 880



################################################################################
## Additional screens
################################################################################


## Confirm screen ##############################################################
##
## The confirm screen is called when Ren'Py wants to ask the player a yes or no
## question: a framed plate between two gold rules.
##
## https://www.renpy.org/doc/html/screen_special.html#confirm

screen confirm(message, yes_action, no_action):

    ## Ensure other screens do not get input while this screen is displayed.
    modal True

    zorder 200

    style_prefix "confirm"

    add Solid("#000000b8")

    frame:

        vbox:
            xalign .5
            yalign .5
            spacing 36

            add "vn_rule" xalign 0.5

            label _(message):
                style "confirm_prompt"
                xalign 0.5

            hbox:
                xalign 0.5
                spacing 120

                textbutton _("Yes") action yes_action
                textbutton _("No") action no_action

    ## Right-click and escape answer "no".
    key "game_menu" action no_action


style confirm_frame is gui_frame
style confirm_prompt is gui_prompt
style confirm_prompt_text is gui_prompt_text
style confirm_button is gui_medium_button
style confirm_button_text is gui_medium_button_text

style confirm_frame:
    background Frame("gui/vn/card_background.png", 12, 12)
    padding (90, 54, 90, 54)
    xalign .5
    yalign .5
    xmaximum 1200

style confirm_prompt_text:
    textalign 0.5
    layout "subtitle"
    size 34
    color "#efe6d2"

style confirm_button:
    properties gui.button_properties("confirm_button")
    padding (40, 8)
    background Frame("gui/vn/nav_[prefix_]background.png", 6, 0)

style confirm_button_text:
    properties gui.text_properties("confirm_button")
    size 36
    kerning 3


## Skip indicator screen #######################################################
##
## The skip_indicator screen is displayed to indicate that skipping is in
## progress.
##
## https://www.renpy.org/doc/html/screen_special.html#skip-indicator

screen skip_indicator():

    zorder 100
    style_prefix "skip"

    frame:

        hbox:
            spacing 9

            text _("Skipping")

            text "▸" at delayed_blink(0.0, 1.0) style "skip_triangle"
            text "▸" at delayed_blink(0.2, 1.0) style "skip_triangle"
            text "▸" at delayed_blink(0.4, 1.0) style "skip_triangle"


## This transform is used to blink the arrows one after another.
transform delayed_blink(delay, cycle):
    alpha .5

    pause delay

    block:
        linear .2 alpha 1.0
        pause .2
        linear .2 alpha 0.5
        pause (cycle - .4)
        repeat


style skip_frame is empty
style skip_text is gui_text
style skip_triangle is skip_text

style skip_frame:
    xpos 36
    ypos gui.skip_ypos
    background Frame("gui/vn/card_background.png", 12, 12)
    padding (28, 12, 28, 12)

style skip_text:
    size 26
    kerning 2
    color gui.accent_color

style skip_triangle:
    ## We have to use a font that has the BLACK RIGHT-POINTING SMALL TRIANGLE
    ## glyph in it.
    font "DejaVuSans.ttf"


## Notify screen ###############################################################
##
## The notify screen is used to show the player a message. (For example, when
## the game is quicksaved or a screenshot has been taken.)
##
## https://www.renpy.org/doc/html/screen_special.html#notify-screen

screen notify(message):

    zorder 100
    style_prefix "notify"

    frame at notify_appear:
        hbox:
            spacing 16
            text vn_diamond style "notify_mark"
            text "[message!tq]"

    timer 3.25 action Hide('notify')


transform notify_appear:
    on show:
        alpha 0
        linear .25 alpha 1.0
    on hide:
        linear .5 alpha 0.0


style notify_frame is empty
style notify_text is gui_text
style notify_mark is notify_text

style notify_frame:
    xalign 0.5
    ypos gui.notify_ypos
    background Frame("gui/vn/card_background.png", 12, 12)
    padding (36, 14, 40, 14)

style notify_text:
    properties gui.text_properties("notify")
    size 28
    color "#efe6d2"

style notify_mark:
    size 16
    color gui.accent_color
    yalign 0.5


## NVL screen ##################################################################
##
## This screen is used for NVL-mode dialogue and menus.
##
## https://www.renpy.org/doc/html/screen_special.html#nvl


screen nvl(dialogue, items=None):

    window:
        style "nvl_window"

        has vbox:
            spacing gui.nvl_spacing

        ## Displays dialogue in either a vpgrid or the vbox.
        if gui.nvl_height:

            vpgrid:
                cols 1
                yinitial 1.0

                use nvl_dialogue(dialogue)

        else:

            use nvl_dialogue(dialogue)

        ## Displays the menu, if given. The menu may be displayed incorrectly if
        ## config.narrator_menu is set to True.
        for i in items:

            textbutton i.caption:
                action i.action
                style "nvl_button"

    add SideImage() xalign 0.0 yalign 1.0


screen nvl_dialogue(dialogue):

    for d in dialogue:

        window:
            id d.window_id

            fixed:
                yfit gui.nvl_height is None

                if d.who is not None:

                    text d.who:
                        id d.who_id

                text d.what:
                    id d.what_id


## This controls the maximum number of NVL-mode entries that can be displayed at
## once.
define config.nvl_list_length = gui.nvl_list_length

style nvl_window is default
style nvl_entry is default

style nvl_label is say_label
style nvl_dialogue is say_dialogue

style nvl_button is button
style nvl_button_text is button_text

style nvl_window:
    xfill True
    yfill True

    background "gui/nvl.png"
    padding gui.nvl_borders.padding

style nvl_entry:
    xfill True
    ysize gui.nvl_height

style nvl_label:
    xpos gui.nvl_name_xpos
    xanchor gui.nvl_name_xalign
    ypos gui.nvl_name_ypos
    yanchor 0.0
    xsize gui.nvl_name_width
    min_width gui.nvl_name_width
    textalign gui.nvl_name_xalign

style nvl_dialogue:
    xpos gui.nvl_text_xpos
    xanchor gui.nvl_text_xalign
    ypos gui.nvl_text_ypos
    xsize gui.nvl_text_width
    min_width gui.nvl_text_width
    textalign gui.nvl_text_xalign
    layout ("subtitle" if gui.nvl_text_xalign else "tex")

style nvl_thought is say_dialogue:
    xpos gui.nvl_thought_xpos
    xanchor gui.nvl_thought_xalign
    ypos gui.nvl_thought_ypos
    xsize gui.nvl_thought_width
    min_width gui.nvl_thought_width
    textalign gui.nvl_thought_xalign
    layout ("subtitle" if gui.nvl_text_xalign else "tex")

style nvl_button:
    properties gui.button_properties("nvl_button")
    xpos gui.nvl_button_xpos
    xanchor gui.nvl_button_xalign

style nvl_button_text:
    properties gui.text_properties("nvl_button")


## Bubble screen ###############################################################
##
## The bubble screen is used to display dialogue to the player when using speech
## bubbles. The bubble screen takes the same parameters as the say screen, must
## create a displayable with the id of "what", and can create displayables with
## the "namebox", "who", and "window" ids.
##
## https://www.renpy.org/doc/html/bubble.html#bubble-screen

screen bubble(who, what):
    style_prefix "bubble"

    window:
        id "window"

        if who is not None:

            window:
                id "namebox"
                style "bubble_namebox"

                text who:
                    id "who"

        text what:
            id "what"

        default ctc = None
        showif ctc:
            add ctc

style bubble_window is empty
style bubble_namebox is empty
style bubble_who is default
style bubble_what is default

style bubble_window:
    xpadding 30
    top_padding 5
    bottom_padding 5

style bubble_namebox:
    xalign 0.5

style bubble_who:
    xalign 0.5
    textalign 0.5
    color "#000"

style bubble_what:
    align (0.5, 0.5)
    text_align 0.5
    layout "subtitle"
    color "#000"

define bubble.frame = Frame("gui/bubble.png", 55, 55, 55, 95)
define bubble.thoughtframe = Frame("gui/thoughtbubble.png", 55, 55, 55, 55)

define bubble.properties = {
    "bottom_left" : {
        "window_background" : Transform(bubble.frame, xzoom=1, yzoom=1),
        "window_bottom_padding" : 27,
    },

    "bottom_right" : {
        "window_background" : Transform(bubble.frame, xzoom=-1, yzoom=1),
        "window_bottom_padding" : 27,
    },

    "top_left" : {
        "window_background" : Transform(bubble.frame, xzoom=1, yzoom=-1),
        "window_top_padding" : 27,
    },

    "top_right" : {
        "window_background" : Transform(bubble.frame, xzoom=-1, yzoom=-1),
        "window_top_padding" : 27,
    },

    "thought" : {
        "window_background" : bubble.thoughtframe,
    }
}

define bubble.expand_area = {
    "bottom_left" : (0, 0, 0, 22),
    "bottom_right" : (0, 0, 0, 22),
    "top_left" : (0, 22, 0, 0),
    "top_right" : (0, 22, 0, 0),
    "thought" : (0, 0, 0, 0),
}



################################################################################
## Mobile Variants
################################################################################

style pref_vbox:
    variant "medium"
    xsize 675

## Since a mouse may not be present, we replace the quick menu with a version
## that uses fewer and bigger buttons that are easier to touch.
screen quick_menu():
    variant "touch"

    zorder 100

    if quick_menu:

        frame:
            style "quick_frame"

            hbox:
                style "quick_menu"
                style_prefix "quick"

                textbutton _("Back") action Rollback()
                textbutton _("Skip") action Skip() alternate Skip(fast=True, confirm=True)
                textbutton _("Auto") action Preference("auto-forward", "toggle")
                textbutton _("Menu") action ShowMenu()


## On touch screens the quick menu has bigger buttons: it goes to the top right
## corner, clear of the dialogue.
style quick_frame:
    variant "touch"
    xalign 1.0
    yalign 0.0

style window:
    variant "small"
    background "gui/phone/textbox.png"

style radio_button:
    variant "small"
    foreground "gui/phone/button/radio_[prefix_]foreground.png"

style check_button:
    variant "small"
    foreground "gui/phone/button/check_[prefix_]foreground.png"

style nvl_window:
    variant "small"
    background "gui/phone/nvl.png"

style navigation_vbox:
    variant "small"
    spacing 4

style navigation_button_text:
    variant "small"
    size 40

style main_menu_vbox:
    variant "small"
    ypos 90

style main_nav_vbox:
    variant "small"
    ypos 500
    spacing 0

style main_nav_button_text:
    variant "small"
    size 46

style game_menu_navigation_frame:
    variant "small"
    xsize 440
    background Fixed(Solid("#0b0907d8"), Solid("#c9a25b50", xsize=1, ysize=960, xpos=439, ypos=60))

style game_menu_work_title:
    variant "small"
    xpos 220

style game_menu_work_rule:
    variant "small"
    xpos 220

style game_menu_title_vbox:
    variant "small"
    xpos 1190
    ypos 30

style game_menu_content_frame:
    variant "small"
    xpos 500
    ypos 200
    xsize 1380
    ysize 840

style slot_fixed:
    variant "small"
    ysize 206

style slot_scene_text:
    variant "small"
    size 40

style slot_who_text:
    variant "small"
    size 32

style slot_line_text:
    variant "small"
    size 32

style slot_time_text:
    variant "small"
    size 30

style slot_empty_text:
    variant "small"
    size 36

style slot_delete_button_text:
    variant "small"
    size 32

style page_button_text:
    variant "small"
    size 38

style pref_vbox:
    variant "small"
    xsize 600

style pref_label_text:
    variant "small"
    size 40

style history_name_text:
    variant "small"
    size 36

style history_text:
    variant "small"
    size 36

style confirm_prompt_text:
    variant "small"
    size 46

style confirm_button_text:
    variant "small"
    size 50

style notify_text:
    variant "small"
    size 38

style about_text:
    variant "small"
    size 38

style bar:
    variant "small"
    ysize gui.bar_size
    left_bar Frame("gui/phone/bar/left.png", gui.bar_borders, tile=gui.bar_tile)
    right_bar Frame("gui/phone/bar/right.png", gui.bar_borders, tile=gui.bar_tile)

style vbar:
    variant "small"
    xsize gui.bar_size
    top_bar Frame("gui/phone/bar/top.png", gui.vbar_borders, tile=gui.bar_tile)
    bottom_bar Frame("gui/phone/bar/bottom.png", gui.vbar_borders, tile=gui.bar_tile)

style scrollbar:
    variant "small"
    ysize gui.scrollbar_size
    base_bar Frame("gui/phone/scrollbar/horizontal_[prefix_]bar.png", gui.scrollbar_borders, tile=gui.scrollbar_tile)
    thumb Frame("gui/phone/scrollbar/horizontal_[prefix_]thumb.png", gui.scrollbar_borders, tile=gui.scrollbar_tile)

style vscrollbar:
    variant "small"
    xsize gui.scrollbar_size
    base_bar Frame("gui/phone/scrollbar/vertical_[prefix_]bar.png", gui.vscrollbar_borders, tile=gui.scrollbar_tile)
    thumb Frame("gui/phone/scrollbar/vertical_[prefix_]thumb.png", gui.vscrollbar_borders, tile=gui.scrollbar_tile)

style slider:
    variant "small"
    ysize gui.slider_size
    base_bar Frame("gui/phone/slider/horizontal_[prefix_]bar.png", gui.slider_borders, tile=gui.slider_tile)
    thumb "gui/phone/slider/horizontal_[prefix_]thumb.png"

style vslider:
    variant "small"
    xsize gui.slider_size
    base_bar Frame("gui/phone/slider/vertical_[prefix_]bar.png", gui.vslider_borders, tile=gui.slider_tile)
    thumb "gui/phone/slider/vertical_[prefix_]thumb.png"

style slider_vbox:
    variant "small"
    xsize None

style slider_slider:
    variant "small"
    xsize 900
