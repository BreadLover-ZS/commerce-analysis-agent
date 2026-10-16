import type { CSSProperties } from "react";

const paths = {
  chart: (
    <>
      <path d="M4 4v16h16" />
      <path d="M8 15v-4m5 4V7m5 8v-6" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  file: (
    <>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6M8 13h8m-8 4h5" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4 4" />
    </>
  ),
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  logout: (
    <>
      <path d="M9 5H5v14h4m5-14 7 7-7 7m-6-7h13" />
    </>
  ),
} as const;

export default function Icon({
  name,
  size = 18,
  style,
}: {
  name: keyof typeof paths;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      {paths[name]}
    </svg>
  );
}
