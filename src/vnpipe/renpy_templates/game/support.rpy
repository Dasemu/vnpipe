## Support code for the generated script: transitions, stage positions, colour
## treatments and the screen shake. Static; copied verbatim by `vnpipe renpy`.

define vn_slow = Dissolve(2.0)
define vn_flash = Fade(0.1, 0.0, 0.6, color="#ffffff")

transform vn_left:
    xalign 0.15 yalign 1.0

transform vn_center:
    xalign 0.5 yalign 1.0

transform vn_right:
    xalign 0.85 yalign 1.0

transform tint_none:
    matrixcolor IdentityMatrix()

transform tint_dark:
    matrixcolor TintMatrix("#8a8498") * BrightnessMatrix(-0.2)

transform tint_red:
    matrixcolor TintMatrix("#f0968a") * BrightnessMatrix(-0.05)

transform vn_shake:
    xoffset 0 yoffset 0
    linear 0.05 xoffset 18 yoffset -8
    linear 0.05 xoffset -18 yoffset 8
    linear 0.05 xoffset 14 yoffset -6
    linear 0.05 xoffset -14 yoffset 6
    linear 0.05 xoffset 8 yoffset -3
    linear 0.05 xoffset 0 yoffset 0

init python:
    config.font_replacement_map["fonts/RobotoSlab-Regular.ttf", True, False] = (
        "fonts/RobotoSlab-Bold.ttf", False, False)

define vn_move = MoveTransition(0.8)
