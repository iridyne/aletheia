
# diagrams/ - Diagram Sources And Renders

## Subfolders

| Folder | Use |
|---|---|
| `source/` | Mermaid and D2 source files |
| `rendered/` | PNG/SVG renders used by README and docs |

## Agent Rule

When changing a diagram, keep source and rendered file aligned if the rendered asset is used by documentation.

## README architecture diagrams (D2)

The root README uses five domain-focused D2 diagrams. Render them from the
repository root with:

```bash
for name in system_overview agent_decision_loop learning_evolution_loop execution_risk data_flow; do
  d2 --layout elk "docs/diagrams/source/readme_${name}.d2" \
    "docs/diagrams/rendered/readme_${name}.svg"
done
d2 --layout elk docs/diagrams/source/graph.d2 \
  docs/diagrams/rendered/readme_complete_architecture.svg
```

Keep each D2 source focused on one architectural question. The diagrams are a
navigation surface, not evidence that every future or experimental path is
runtime-proven; describe capability boundaries explicitly in the source.

`interactive/index.html` supplies pan, wheel/pinch zoom, fit-to-window, and a
direct SVG view for the complete `graph.d2` render. GitHub README pages sanitize
scripts and iframes, so the README links to this viewer on GitHub Pages instead
of pretending embedded Markdown can execute the controls.
