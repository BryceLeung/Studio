import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';

// An editable scaffold, not a generated interpretation of the asset brief.
export default function Animation({title = 'Animation draft'}: {title?: string}) {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{backgroundColor: '#101820', color: '#f5f1e8', justifyContent: 'center', alignItems: 'center', fontFamily: 'sans-serif'}}>
      <div style={{opacity, fontSize: 80, textAlign: 'center', padding: 60}}>{title}</div>
      <div style={{fontSize: 24}}>Draft scaffold: compose the requested visual before review.</div>
    </AbsoluteFill>
  );
}
