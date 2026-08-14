import React, { useState } from "react";
import { CheckCircle2, XCircle, ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCall } from "../lib/api-types";

export const ToolCallBlock: React.FC<{ call: ToolCall }> = ({ call }) => {
  const [open, setOpen] = useState(false);
  const ok = call.result?.ok !== false;
  return (
    <div className="rounded-xl bg-white/60 border border-black/5 px-3 py-1.5 text-[12px]">
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left flex items-center gap-2"
      >
        {ok ? (
          <CheckCircle2 size={13} className="text-phc-green" />
        ) : (
          <XCircle size={13} className="text-red-500" />
        )}
        <Wrench size={12} className="text-zinc-400" />
        <span className="text-phc-ink font-medium">{call.name}</span>
        {call.duration_ms && (
          <span className="text-zinc-500 text-[10px] ml-auto font-mono">
            {(call.duration_ms / 1000).toFixed(1)}s
          </span>
        )}
        {open ? (
          <ChevronDown size={12} className="text-zinc-400" />
        ) : (
          <ChevronRight size={12} className="text-zinc-400" />
        )}
      </button>
      {open && (
        <div className="mt-1.5 pt-1.5 border-t border-black/5 text-[11px] space-y-1">
          {Object.keys(call.args || {}).length > 0 && (
            <div>
              <div className="text-zinc-500 mb-0.5">args:</div>
              <pre className="whitespace-pre-wrap text-zinc-700 font-mono leading-snug">
                {JSON.stringify(call.args, null, 2).slice(0, 500)}
              </pre>
            </div>
          )}
          {call.result && (
            <div>
              <div className="text-zinc-500 mb-0.5">result:</div>
              <pre className="whitespace-pre-wrap text-zinc-700 font-mono leading-snug">
                {JSON.stringify(call.result, null, 2).slice(0, 500)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
