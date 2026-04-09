import React from 'react';

interface SparklineProps {
  /** Ordered list of numeric values to plot (oldest first). */
  points: number[];
  /** Width of the SVG in CSS pixels. */
  width?: number;
  /** Height of the SVG in CSS pixels. */
  height?: number;
  /** Stroke colour via Tailwind text-* utility (e.g. 'text-ios-blue'). */
  color?: string;
  /** Fill below the line using currentColor at this opacity (0-1). */
  fillOpacity?: number;
  /** Optional constant y-reference line (e.g. SLA target). */
  referenceY?: number;
  /** Accessible label read by screen readers. */
  label?: string;
}

/**
 * Sparkline — tiny inline trend chart for KPI cards.
 *
 * Hand-rolled polyline (no dependency) because we only ever render
 * 7-30 points per card. Auto-scales the y-axis to the data range with
 * a 5% padding so the line never touches the edges. Renders nothing
 * (returns `null`) for < 2 points so callers can pass an empty/loading
 * series without a wrapper conditional.
 */
export const Sparkline: React.FC<SparklineProps> = ({
  points,
  width = 100,
  height = 32,
  color = 'text-ios-blue',
  fillOpacity = 0.12,
  referenceY,
  label,
}) => {
  if (!points || points.length < 2) return null;

  const pad = 2;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const min = Math.min(...points, referenceY ?? Infinity);
  const max = Math.max(...points, referenceY ?? -Infinity);
  const range = max - min || 1; // guard against flat series
  const scaleY = (v: number) => pad + innerH - ((v - min) / range) * innerH;
  const stepX = innerW / (points.length - 1);

  const line = points.map((v, i) => `${pad + i * stepX},${scaleY(v)}`).join(' ');
  // Build the fill polygon: same points plus anchors at the bottom corners
  // so the area under the line renders cleanly.
  const fill = `${pad},${pad + innerH} ${line} ${pad + innerW},${pad + innerH}`;

  const refY = referenceY !== undefined ? scaleY(referenceY) : null;

  return (
    <svg
      width={width}
      height={height}
      className={color}
      role="img"
      aria-label={label || 'Trend sparkline'}
    >
      <polygon points={fill} fill="currentColor" fillOpacity={fillOpacity} />
      {refY !== null && (
        <line
          x1={pad}
          x2={pad + innerW}
          y1={refY}
          y2={refY}
          stroke="currentColor"
          strokeDasharray="2 2"
          strokeWidth={1}
          opacity={0.5}
        />
      )}
      <polyline
        points={line}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

export default Sparkline;
