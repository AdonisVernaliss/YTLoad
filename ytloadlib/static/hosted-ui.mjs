export function selectedEstimate(inspection, quality, container) {
  if (!inspection?.size_estimates) return null;
  const policy = container === 'mp4' || quality === 'compatible' ? 'compatible' : 'source';
  return inspection.size_estimates[policy]?.[quality] || null;
}

export function estimateState(estimate) {
  if (!estimate || typeof estimate.bytes !== 'number') return 'unknown';
  if (estimate.over_limit) return 'over';
  if (estimate.near_limit) return 'near';
  return 'available';
}

export function directLink(candidate) {
  if (!candidate?.url) return null;
  return { href: candidate.url, target: '_blank', rel: 'noopener noreferrer', referrerPolicy: 'no-referrer' };
}
