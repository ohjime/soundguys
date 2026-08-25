# Explore article fonts

Each subfolder containing both `font.json` and `font.css` automatically becomes
an Article font option in the Explore post admin. Vite bundles every matching
`font.css` file and its referenced font files.

A font folder contains:

- one or more `.woff2` font files;
- the font's license;
- `font.css` with its `@font-face` rules; and
- `font.json` with this shape:

```json
{
  "id": "folder-name",
  "label": "Font label in the admin",
  "family": "CSS Font Family",
  "category": "serif"
}
```

The `id` must match the folder name. Supported categories are `serif`,
`sans-serif`, `cursive`, and `monospace`.

Restart the Vite development server after adding a folder, or run
`npm run build` for production. The Django admin reads manifests on every
request, so it does not need a code change. If a selected manifest is removed,
the post falls back to Tailwind Sans.
