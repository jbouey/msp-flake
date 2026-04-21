import { useState } from "react";

type Props = {
  template: string;
  details: Record<string, unknown>;
};

function substitute(template: string, details: Record<string, unknown>): string {
  return template.replace(/\{([a-z_]+)\}/g, (_, key) => {
    const v = details[key];
    return v === undefined || v === null ? `{${key}}` : String(v);
  });
}

export default function CopyCliButton({ template, details }: Props) {
  const [copied, setCopied] = useState(false);
  if (!template) return null;
  const cmd = substitute(template, details);
  const handle = async () => {
    await navigator.clipboard.writeText(cmd);
    setCopied(true);
    setTimeout(() => setCopied(false), 3000);
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={handle}
        className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20"
      >Copy CLI</button>
      {copied && (
        <span className="text-xs text-amber-200">
          Copied — run under your own --actor-email
        </span>
      )}
    </span>
  );
}
