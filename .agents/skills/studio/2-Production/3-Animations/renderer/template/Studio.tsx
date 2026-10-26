import React from 'react';
import {Composition, registerRoot} from 'remotion';
import Animation from './Composition';

// Keep metadata inline so Studio can edit and save it.
// The candidate render uses this same root and captures its resolved metadata.
const StudioRoot = () => (
  <Composition
    id="Animation"
    component={Animation}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={150}
  />
);

registerRoot(StudioRoot);
