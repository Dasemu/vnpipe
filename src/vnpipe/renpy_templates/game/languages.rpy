## Languages: the play's own text, plus every translation built by `vnpipe translate`
## (game/tl/<language>/). The player picks one in Preferences, or at first launch; the
## choice is kept in `persistent.vn_language`. `config.language` is set from that choice
## only, so a language preference left by an older build never overrides it, and loading a
## save keeps the chosen language. Static except for the substituted list.

default persistent.vn_language = None

## (Ren'Py language, label in that language) pairs; the first is the default.
define vn_languages = @@LANGUAGES@@

init 1 python:

    def vn_known_language(language):
        return any(lang == language for lang, _label in vn_languages)

    def vn_set_language(language):
        """Remember the player's choice; Language(language) then switches to it."""
        persistent.vn_language = language
        config.language = language

    if vn_known_language(persistent.vn_language):
        config.language = persistent.vn_language
    else:
        config.language = vn_languages[0][0]
    config.default_language = vn_languages[0][0]
