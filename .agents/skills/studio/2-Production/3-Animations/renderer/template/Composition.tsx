import React from 'react';
import {AbsoluteFill, Interactive, interpolate, useCurrentFrame, type InteractivitySchema} from 'remotion';

// Editable scaffold only. Replace the demonstration art with the approved brief.
// Runtime defaults are required: Studio removes props when reset to their defaults.
function GraphicInner({title = 'Animation draft', color = '#d6ae61', style}: {
  title?: string; color?: string; style?: React.CSSProperties;
}) {
  return <svg viewBox="0 0 1920 1080" width="100%" height="100%" style={style}>
    <circle cx="960" cy="440" r="120" fill={color}/>
    <text x="960" y="690" textAnchor="middle" fill="#423729" fontFamily="sans-serif" fontSize="64">{title}</text>
    <text x="960" y="770" textAnchor="middle" fill="#746653" fontFamily="sans-serif" fontSize="28">Draft scaffold: author the requested visual before review.</text>
  </svg>;
}

const Graphic = Interactive.create({
  Component: GraphicInner,
  schema: {
    title: {type: 'string', default: 'Animation draft', description: 'Title'},
    color: {type: 'color', default: '#d6ae61', description: 'Accent color'},
  } satisfies InteractivitySchema,
});

export default function Animation() {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{backgroundColor: '#f5efe3'}}>
    <Graphic
      name="Draft graphic · replace with approved artwork"
      from={0}
      title="Animation draft"
      color="#d6ae61"
      style={{
        opacity: interpolate(frame, [0, 15], [0, 1], {
          extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        }),
      }}
    />
  </AbsoluteFill>;
}
