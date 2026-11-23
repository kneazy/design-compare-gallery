# design-compare-gallery

A [Claude Code](https://claude.com/claude-code) skill that builds a local browser gallery comparing Figma frames with your implemented web pages, section by section.

Each gallery entry puts the Figma section and the same section of your page side by side, at the same scale. You can compare them by:

- swipe, opacity, difference or side-by-side views;
- drag-to-align;
- desktop and mobile widths, light and dark themes;
- a "before fixes / now" toggle that shows which build each set came from.

It also offers:

- an optional per-section pixel-perfect scoreboard;
- designer questions with answer chips;
- a strict build-vs-build regression check covering pixels, `<head>`, status and console.

## Install

```bash
git clone https://github.com/kneazy/design-compare-gallery ~/.claude/skills/design-compare-gallery
```

## Requirements

- Python 3 with Pillow, and Node 18+.
- A Figma personal access token in `FIGMA_TOKEN`.
- **Playwright, pixelmatch and pngjs**, loaded from `~/.claude/skills/pixel-perfect/scripts`. That folder is the companion `pixel-perfect` skill. If you don't have it, a plain folder there with those packages is enough for the gallery itself; the per-section scoreboard needs the full skill:

  ```bash
  mkdir -p ~/.claude/skills/pixel-perfect/scripts && cd $_
  npm init -y && npm i playwright@1.61.1 pixelmatch@7 pngjs@7 && npx playwright install chromium-headless-shell
  ```

Self-test (no browser needed): `python3 ~/.claude/skills/design-compare-gallery/scripts/dcg.py test`.

## Usage

Ask Claude Code something like "compare the site with Figma visually" or "show me the differences with the mockup". `SKILL.md` covers the config (`assets/design-compare.example.json`), the commands and how to read the results.

## License

MIT
