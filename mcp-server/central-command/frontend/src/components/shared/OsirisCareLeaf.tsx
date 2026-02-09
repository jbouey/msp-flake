import React from 'react';

interface OsirisCareLeafProps {
  className?: string;
  color?: string;
}

/**
 * OsirisCare brand leaf mark — two overlapping leaves in a sprouting motif.
 * Matches the logo's teal leaf silhouette.
 */
export const OsirisCareLeaf: React.FC<OsirisCareLeafProps> = ({
  className = 'w-6 h-6',
  color = 'currentColor',
}) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    {/* Back leaf — slightly larger, rotated right */}
    <path
      d="M12 3C7 3 3 7.5 3 12.5c0 3 1.5 5.5 4 7 .5-4.5 3-8 7-10 .5-.3 1 .2.7.7-2.5 4-3.5 8-3.2 11.3C12.2 21.8 13 22 14 22c5 0 8-4 8-9C22 7 17 3 12 3z"
      fill={color}
      opacity={0.35}
    />
    {/* Front leaf — primary, rotated left */}
    <path
      d="M13 2C8.5 2 4 5.5 4 10.5c0 4 2.5 7.5 6.5 9 .3-5 2.5-9.5 6.5-12.5.5-.4 1.1.1.8.7C15.5 12 14 16.5 13.5 21c.5.1 1 .1 1.5.1C20 21.1 22 17 22 13 22 7 18 2 13 2z"
      fill={color}
    />
  </svg>
);

export default OsirisCareLeaf;
