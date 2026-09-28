
# diagrams/ - Diagram Sources And Renders

## Subfolders

| Folder | Use |
|---|---|
| `source/` | D2 source files for the current diagrams |
| `rendered/` | SVG renders used by README and docs |

## Agent Rule

When changing a diagram, keep source and rendered file aligned if the rendered asset is used by documentation. Only the five `readme_*.d2` sources below are current; the
former Mermaid family lives in [`design/history/diagrams/`](../design/history/diagrams/README.md).

## Architecture diagrams (D2)

Render the domain-focused D2 diagrams from the repository root with:

```bash
for name in system_overview agent_decision_loop learning_evolution_loop execution_risk data_flow; do
  d2 --layout elk "docs/diagrams/source/readme_${name}.d2" \
    "docs/diagrams/rendered/readme_${name}.svg"
done
```

Keep each D2 source focused on one architectural question. The diagrams are a
navigation surface, not evidence that every future or experimental path is
runtime-proven; describe capability boundaries explicitly in the source.

The former complete architecture and Operator-entry diagrams are
[archived with their original sources and renders](../design/history/diagrams/README.md).
Their pan-and-zoom viewer is also archived. The current execution diagram follows
Entry Plan → Gateway → Risk approval → external account; an account mode being registered
is separate from configuration, admission and runtime qualification.
