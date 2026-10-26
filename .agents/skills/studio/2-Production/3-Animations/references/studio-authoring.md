# Authoring animations for Remotion Studio

Generate the authored artwork and its editable Studio structure together. Preserve the asset's entry/configuration files and use the same composition for preview and registered rendering. Design controls for the specific asset; a typographic hold needs different controls from a camera reveal or chart.

## Timeline structure

- Give independently adjustable elements their own explicit JSX node and a descriptive `name`. Use `Interactive` SVG/HTML elements, `Sequence`, or custom components wrapped with `Interactive.create()`.
- A generated collection can remain one grouped layer when it is edited as a whole. Use separate nodes for elements that need distinct timing, selection or deletion; a `.map()` does not provide independent source nodes.
- Use `layout: 'none'` for custom SVG nodes and forward `style` to the visual root. Use `layout: 'absolute-fill'` for a full-frame scene. A `Sequence` nested inside an SVG needs `layout="none"`.
- Time a clip's entrance from its local `useCurrentFrame()` when moving its timeline start should move the animation. Passing an unrelated parent's absolute clock can make a draggable start appear to do nothing.

## Controls and keyframes

Use Remotion's `InteractivitySchema` for useful custom fields such as camera duration, zoom, labels, color, drop height or bounce. Keep editable values inline at their JSX call site. Forward styles where the selected component supports canvas transforms.

Every control must also have a runtime default in the component. Studio may remove the JSX prop when the user resets it to the schema default; the schema's default is not a substitute for the component's runtime fallback.

```tsx
function BadgeInner({color = '#d6ae61', style}: {
  color?: string; style?: React.CSSProperties;
}) {
  return <g style={style}><circle r={20} fill={color}/></g>;
}
```

For native keyframe editing, keep `interpolate(frame, ...)`, its ranges/easing and CSS `scale`, `translate`, `rotate` or `opacity` inline on an interactive node. A complex derived transform can instead have a few clear custom controls. Disable keyframing for parameters whose meaning is a whole-shot constant; do not expose a keyframe button that the component cannot correctly evaluate.

Keep composition metadata inline in `Studio.tsx` where practical. If duration must depend on controls, implement a pure `calculateMetadata` from explicit data/props. Do not call an animation component from metadata code: subsequent visual edits may add hooks or frame-dependent expressions to that component.

## Preview and export

The asset contains `src/Composition.tsx`, `src/Studio.tsx`, `package.json`, `tsconfig.json`, `settings.json` and `references/`. It has no separate dependency installation or project `node_modules` link. The maintained launcher supplies the shared renderer modules while using the real asset folder as the editable root.

`animations.py render` resolves the Studio entry's metadata, detects inputs changing during inspection, captures the resolved technical settings, and snapshots the source/configuration before rendering. It reuses a current registered render without launching another metadata browser. Do not bypass this path to turn a Studio export into an approved candidate.

Before handing over a newly authored control surface, verify playback and a meaningful change/reset. Check that the changed value saves back to source, a reset still renders correctly, and the relevant clip actually responds to a timing edit. Use an isolated test project when changing shared scaffolding or renderer mechanics.

Official references: [interactive authoring](https://www.remotion.dev/docs/studio/interactivity-best-practices), [custom components](https://www.remotion.dev/docs/studio/make-component-interactive), [visual controls](https://www.remotion.dev/docs/visual-editing).
