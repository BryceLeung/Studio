export const validateSettings = (settings) => {
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
    throw new Error('Settings must be a JSON object.');
  }
  for (const key of ['width', 'height']) {
    if (!Number.isInteger(settings[key]) || settings[key] < 16 || settings[key] > 7680 || settings[key] % 2 !== 0) {
      throw new Error(`${key} must be an even integer between 16 and 7680 for H.264.`);
    }
  }
  if (!Number.isFinite(settings.fps) || settings.fps < 1 || settings.fps > 120) {
    throw new Error('fps must be between 1 and 120.');
  }
  if (!Number.isInteger(settings.durationInFrames) || settings.durationInFrames < 1 || settings.durationInFrames > 108000) {
    throw new Error('durationInFrames must be an integer between 1 and 108000.');
  }
  if (settings.props !== undefined && (!settings.props || typeof settings.props !== 'object' || Array.isArray(settings.props))) {
    throw new Error('props must be a JSON object.');
  }
  return settings;
};
