# Bundled Fonts — DejaVu Sans

## Source

- Repository: https://github.com/dejavu-fonts/dejavu-fonts
- Release tag: https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37
- Version: **2.37**
- Archive: `dejavu-fonts-ttf-2.37.tar.bz2`

## Files

| File | Purpose |
|------|---------|
| `DejaVuSans.ttf` | Regular weight — body text in PDF reports |
| `DejaVuSans-Bold.ttf` | Bold weight — table headers, section titles |
| `LICENSE.txt` | Bitstream Vera Public License + DejaVu additions (public domain) |

## Why bundled

ReportLab's built-in Helvetica (Type 1) covers only the standard Latin-1 character set.
This project's labor data contains:

- **Vietnamese worker names** — diacritics: ă, â, đ, ê, ô, ơ, ư + 5 tone marks (e.g. Nguyễn Thị Hương)
- **French text** — è, à, é, ê, î, ô, ù, û, ü, ç
- **English** — basic ASCII

DejaVu Sans covers Unicode BMP (plane 0) and provides correct rendering for all three scripts
without runtime system font dependencies. The TTFs are registered via ReportLab's `pdfmetrics`
in `pdf_builder.py` using `pdfmetrics.registerFont(TTFont(...))`.

## License

Bitstream Vera Fonts Copyright: Copyright (c) 2003 by Bitstream, Inc.
DejaVu changes are in public domain. See `LICENSE.txt` for full text.
License is compatible with Apache 2.0 and MIT — safe to bundle in commercial applications.

## How to update

1. Check https://github.com/dejavu-fonts/dejavu-fonts/releases for a newer version tag.
2. Run:
   ```bash
   curl -L -o /tmp/dejavu.tar.bz2 \
     https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_X_YZ/dejavu-fonts-ttf-X.YZ.tar.bz2
   tar -xjf /tmp/dejavu.tar.bz2 -C /tmp
   cp /tmp/dejavu-fonts-ttf-X.YZ/ttf/DejaVuSans.ttf      app/domain/labor/export/fonts/
   cp /tmp/dejavu-fonts-ttf-X.YZ/ttf/DejaVuSans-Bold.ttf app/domain/labor/export/fonts/
   cp /tmp/dejavu-fonts-ttf-X.YZ/LICENSE                  app/domain/labor/export/fonts/LICENSE.txt
   ```
3. Update the version number in this README.
4. Commit with message: `chore(fonts): update DejaVu Sans to X.YZ`
