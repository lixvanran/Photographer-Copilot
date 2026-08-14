import React, { useEffect, useRef } from "react";
import { Send, Camera } from "lucide-react";
import type { ChatMessage } from "../lib/api-types";
import { ToolCallBlock } from "./ToolCallBlock";
import { MiniMarkdown } from "./MiniMarkdown";

interface Props {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  disabled?: boolean;
  streamingText?: string;
}

export const ChatBox: React.FC<Props> = ({ messages, onSend, disabled, streamingText }) => {
  const [input, setInput] = React.useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, streamingText]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || disabled) return;
    onSend(input.trim());
    setInput("");
  };

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
        {messages.length === 0 && !streamingText && (
          <div className="flex flex-col items-center justify-center h-full text-zinc-500 text-sm gap-3">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-phc-accent/20 to-phc-sky/20 flex items-center justify-center">
              <Camera size={28} className="text-phc-accent" />
            </div>
            <div className="text-zinc-700 font-medium">开始一次摄影对话</div>
            <div className="text-xs text-center max-w-md leading-relaxed">
              点左边栏的 <strong className="text-phc-accent">「一键修图」/「一键筛片」</strong> 批量处理照片,或者直接问我摄影问题,比如「什么是光圈优先?」
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            {m.role === "user" ? (
              // User messages: render as plain text (no markdown), keep the
              // dark bubble + preserve line breaks.
              <div className="max-w-[80%] px-4 py-2.5 rounded-2xl bg-phc-ink text-white text-[13px] leading-relaxed whitespace-pre-wrap shadow-sm">
                {m.text}
              </div>
            ) : (
              <div className="max-w-[80%] space-y-1.5">
                {m.text && (
                  <div className="px-4 py-2.5 rounded-2xl bg-white/80 backdrop-blur border border-black/5 text-phc-ink text-[13px] leading-relaxed">
                    <MiniMarkdown source={m.text} />
                  </div>
                )}
                {m.toolCalls?.map((tc, j) => (
                  <ToolCallBlock key={j} call={tc} />
                ))}
              </div>
            )}
          </div>
        ))}

        {streamingText && (
          <div className="flex justify-start">
            <div className="max-w-[80%] px-4 py-2.5 rounded-2xl bg-white/80 backdrop-blur border border-black/5 text-phc-ink text-[13px] leading-relaxed">
              <MiniMarkdown source={streamingText} />
              <span className="inline-block w-1.5 h-3.5 bg-phc-accent ml-0.5 animate-pulse" />
            </div>
          </div>
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="border-t border-black/5 px-4 py-3 flex gap-2 bg-white/40 backdrop-blur"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="问摄影问题,或描述想做什么..."
          disabled={disabled}
          className="flex-1 bg-white/80 border border-black/10 rounded-full px-4 py-2 text-sm
                     focus:outline-none focus:border-phc-accent focus:ring-2 focus:ring-phc-accent/20
                     placeholder:text-zinc-400
                     disabled:opacity-50 transition"
        />
        <button
          type="submit"
          disabled={disabled || !input.trim()}
          className="apple-btn apple-btn-primary"
        >
          <Send size={14} />
          发送
        </button>
      </form>
    </div>
  );
};
