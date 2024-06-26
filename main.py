import hashlib
import os
import re
import markdown2
import anki
from urllib.parse import unquote
from deckConsts import DECKS, OUTPUT_DIR, IGNORE_KEYWORDS


# iterate through all markdown files in directory, ignoring files that begin with _.
# then, read yaml frontmatter and ignore files that have "imported" set to true.
# finally, parse the markdown into anki cards and import them using the AnkiConnect api


def parse_markdown(content, deck_name, tag, media_root):
    def create_card(t, e):
        def pre_process(input_string):
            input_string = input_string.strip()
            sub = "}}"
            while sub in input_string:
                input_string = input_string.replace(sub, "} }")
            return input_string

        t = pre_process(t)
        e = pre_process(e)

        # process clozes
        cloze_id = 1
        bold_matches = re.findall(r"\*\*(.*?)\*\*", t)
        for bold_text in bold_matches:
            cloze_text = bold_text
            if not re.match(r"^\d+::.*", bold_text):
                cloze_text = f"{cloze_id}::{bold_text}"
                cloze_id += 1
            cloze_text = f"{{{{c{cloze_text}}}}}"

            t = t.replace(f"**{bold_text}**", cloze_text)

        def post_process(s):
            s = markdown2.markdown(
                s,
                extras=[
                    # Allows a code block to not have to be indented by fencing it with '```' on a line before and after
                    # Based on http://github.github.com/github-flavored-markdown/ with support for syntax highlighting.
                    "fenced-code-blocks",
                    # tables: Tables using the same format as GFM and PHP-Markdown Extra.
                    "tables",
                    # cuddled-lists: Allow lists to be cuddled to the preceding paragraph.
                    "cuddled-lists",
                    # code-friendly: The code-friendly extra disables the use of leading, trailing and
                    # --most importantly-- intra-word emphasis (<em>) and strong (<strong>)
                    # using single or double underscores, respectively.
                    "code-friendly",
                    # footnotes: support footnotes as in use on daringfireball.net and implemented in other
                    # Markdown processors (tho not in Markdown.pl v1.0.1).
                    "footnotes",
                    # smarty-pants: Fancy quote, em-dash and ellipsis handling similar to
                    # http://daringfireball.net/projects/smartypants/. See old issue 42 for discussion.
                    "smarty-pants",
                    # target-blank-links: Add target="_blank" to all <a> tags with an href.
                    # This causes the link to be opened in a new tab upon a click.
                    "target-blank-links",
                ],
            )

            s = s.replace("<p>", "").replace("</p>", "")

            # process latex
            ml_latex = re.findall(r"\$\$(.*?)\$\$", s)
            for latex in ml_latex:
                latex = latex.replace("}}", "} }")
                s = s.replace(f"$${latex}$$", f"\\[{latex}\\]")

            latex = re.findall(r"\$(.*?)\$", s)
            for l in latex:
                s = s.replace(f"${l}$", f"\\({l}\\)")

            # process images
            images = re.findall(r'<img src="(.*?)"', s)

            def hash_file(path):
                BUFF_SIZE = 65536  # read in 64kb chunks

                sha1 = hashlib.sha1()

                with open(path, "rb") as f:
                    while True:
                        data = f.read(BUFF_SIZE)
                        if not data:
                            break
                        sha1.update(data)

                return sha1.hexdigest()

            for image in images:
                image_path = os.path.join(
                    media_root, unquote(image).replace("/", os.sep)
                )
                _, ext = os.path.splitext(image_path)

                image_id = hash_file(image_path)
                filename = f"{image_id}{ext}"

                anki.send_media({"filename": filename, "path": image_path})

                s = s.replace(image, filename)

            return s.strip("\n")

        t = post_process(t)
        e = post_process(e)

        new_line = "<br />"
        t = t.replace("\n", new_line).replace(f">{new_line}<", "> <")
        e = e.replace("\n", new_line)

        # print(f"Creating card with text: {t}")
        # print(f"Creating card with extra: {e}")

        return {
            "deckName": deck_name,
            "modelName": "cloze",
            "fields": {"Text": t, "Extra": e},
            "tags": [tag],
            "options": {
                "allowDuplicate": False,
                "duplicateScope": deck_name,
                "duplicateScopeOptions": {
                    "deckName": deck_name,
                    "checkChildren": False,
                    "checkAllModels": False,
                },
            },
        }

    content = content.split("\n")

    text = ""
    extra = ""

    all = []

    # is building multi line extra
    is_building_ml_extra = False

    is_building_code = False

    append = False
    for line in iter(content):
        # only strip on right to prevent stripping of indent/extra indicator
        line = line.rstrip()

        if line.lstrip() == "+":
            text += "\n"
            text += "\n"
            continue

        if line.startswith("```"):
            if is_building_ml_extra:
                extra += line
                extra += "\n"
            else:
                text += line
                text += "\n"
            is_building_code = not is_building_code
            continue

        if is_building_code:
            if is_building_ml_extra:
                extra += line
                extra += "\n"
            else:
                text += line
                text += "\n"
            continue

        if line == "---":
            if is_building_ml_extra:
                all.append(create_card(text, extra))
                text = ""
                extra = ""
                append = False

            is_building_ml_extra = not is_building_ml_extra
            if is_building_ml_extra:
                append = False
            continue

        if line == "":
            if not is_building_ml_extra:
                append = True
            continue

        if append:
            if text != "":
                all.append(create_card(text, extra))
                append = False
            text = ""
            extra = ""

        if is_building_ml_extra or line.startswith("\t") or line.startswith(" "):
            append = False
            extra += line.lstrip()
            extra += "\n"
        else:
            append = False
            text += line
            text += "\n"

    if text != "":
        all.append(create_card(text, extra))

    return all


def main():
    # Iterate over the specified deck names and directories
    for deck_name, deck_directory in DECKS.items():
        print(deck_directory)
        # Process each note file in the current deck directory
        for root, dirs, files in os.walk(deck_directory):
            for file in files:
                if root.split(os.sep)[-1].startswith(IGNORE_KEYWORDS):
                    print(f"Skipping {file}")
                    continue

                all_cards = []
                # Process only Markdown files and ignore files starting with '_'
                if not file.startswith("_") and file.endswith(".md"):
                    file_path = os.path.join(root, file)

                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                        # isolate yaml stuff
                        if content.startswith("---"):
                            part = content[3:]
                            last_index = part.index("---")
                            content = part[last_index + 4:]

                        rstripped_content = content.rstrip("\n ")
                        if rstripped_content.endswith("***"):
                            continue

                        imported_parts = content.split("***")
                        content = imported_parts[-1]

                        print(f"Processing {file}")
                        tag = "#"
                        tag += "::#".join(deck_name.replace(" ", "").split("::"))

                        last_path = file_path.replace(deck_directory, "").replace(
                            ".md", ""
                        )

                        # remove leading/trailing slashes
                        tag_path = last_path.strip(os.sep)
                        # replace slashes with double colons
                        tag_path = tag_path.replace(os.sep, "::")
                        # remove spaces
                        tag_path = tag_path.replace(" ", "")
                        # replace dashes with sub tag
                        tag_path = tag_path.replace("-", "::")

                        tag += "::"
                        tag += tag_path

                        cards = parse_markdown(content, deck_name, tag, root)

                        all_cards.extend(cards)

                    # import cards using AnkiConnect api
                    rejected = anki.send_notes(all_cards)

                    if rejected is None:
                        # anki connect is not running
                        return None

                    if rejected:
                        base_file_name = "anki-import-error"
                        file_extension = ".txt"
                        counter = 1

                        while os.path.exists(
                                f"{base_file_name}_{counter}{file_extension}"
                        ):
                            counter += 1

                        file_name = f"{base_file_name}_{counter}{file_extension}"

                        with open(
                                os.path.join(OUTPUT_DIR, file_name), "w"
                        ) as error_file:
                            error_file.write("\n".join(rejected))

                        print(f"Output written to {file_name}")
                        continue

                    with open(file_path, "a", encoding="utf-8") as f:
                        # count number of new line characters at end of file
                        counter = 0
                        while content.endswith("\n"):
                            content = content[:-1]
                            counter += 1

                        if counter < 2:
                            f.write("\n\n")
                        f.write("***\n")


if __name__ == "__main__":
    main()
    print("Complete.")
