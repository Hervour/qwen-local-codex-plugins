# Storyboard schema

The Manga Drawer workflow accepts a JSON file with this shape:

```json
{
  "project": "short_project_name",
  "style": "visual style shared by every panel",
  "continuity": "optional story or continuity instructions shared by every panel",
  "character": "character description shared by every panel",
  "negative_prompt": "things to avoid",
  "character_sheet": "optional/path/to/reference.png",
  "base_seed": 100000,
  "defaults": {
    "width": 768,
    "height": 768,
    "resolution": 768
  },
  "panels": [
    {
      "id": "p01",
      "prompt": "what happens in this panel",
      "dialogue": ["Optional bubble text"],
      "frame": "optional/path/to/rough.png",
      "seed": 100001,
      "width": 768,
      "height": 768
    }
  ]
}
```

Paths are resolved relative to the storyboard file unless they are absolute. Missing frame and character-sheet images are replaced with neutral placeholder graphics. A panel-level `seed` overrides `base_seed`.

Keep a project to one to four panels for a first test. Increase `steps` only after the composition and character consistency look correct.
