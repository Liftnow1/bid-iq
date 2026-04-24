"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";

interface Source {
  id: number;
  title: string;
  category: string;
  summary: string | null;
  tags: string[] | null;
  source_filename: string | null;
  source_path: string | null;
  extractor_version: string | null;
  brand_id: number | null;
  brand_name: string | null;
  created_at: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  queryMode?: "cert-inclusive" | "commercial-only";
}

const CATEGORY_LABELS: Record<string, string> = {
  "product-specifications": "Product Specs",
  "competitive-intelligence": "Competitive Intel",
  "pricing-data": "Pricing",
  "bid-history": "Bid History",
  "installation-guides": "Installation",
  "manufacturer-info": "Manufacturer",
  "service-procedures": "Service",
  "compliance-certifications": "Compliance",
  "customer-intelligence": "Customer Intel",
  general: "General",
};

const CATEGORY_COLORS: Record<string, string> = {
  "product-specifications": "bg-blue-500/20 text-blue-300 border-blue-500/30",
  "competitive-intelligence": "bg-red-500/20 text-red-300 border-red-500/30",
  "pricing-data": "bg-green-500/20 text-green-300 border-green-500/30",
  "bid-history": "bg-purple-500/20 text-purple-300 border-purple-500/30",
  "installation-guides": "bg-orange-500/20 text-orange-300 border-orange-500/30",
  "manufacturer-info": "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  "service-procedures": "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  "compliance-certifications": "bg-pink-500/20 text-pink-300 border-pink-500/30",
  "customer-intelligence": "bg-indigo-500/20 text-indigo-300 border-indigo-500/30",
  general: "bg-gray-500/20 text-gray-300 border-gray-500/30",
};

function SourceCard({ source, messageIdx, sourceIdx }: { source: Source; messageIdx: number; sourceIdx: number }) {
  const [expanded, setExpanded] = useState(false);
  const contentId = `source-${messageIdx}-${sourceIdx}-content`;
  const locator = source.source_filename || source.source_path;
  const categoryClass = CATEGORY_COLORS[source.category] || CATEGORY_COLORS.general;
  const categoryLabel = CATEGORY_LABELS[source.category] || source.category;

  return (
    <div className="bg-gray-900/60 border border-gray-700 rounded-lg hover:border-gray-500 transition-colors">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls={contentId}
        className="w-full text-left px-4 py-3 flex items-start gap-3 focus:outline-none focus:ring-2 focus:ring-blue-500/40 rounded-lg"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`px-2 py-0.5 rounded text-xs font-medium border ${categoryClass}`}>
              {categoryLabel}
            </span>
            {source.brand_name && (
              <span className="text-xs text-gray-400">{source.brand_name}</span>
            )}
          </div>
          <div className="text-sm text-gray-200 truncate">{source.title}</div>
          {locator && (
            <div className="text-xs text-gray-500 mt-0.5 truncate">{locator}</div>
          )}
        </div>
        <span
          aria-hidden="true"
          className={`text-gray-500 text-sm mt-0.5 transition-transform ${expanded ? "rotate-45" : ""}`}
        >
          +
        </span>
      </button>

      {expanded && (
        <div
          id={contentId}
          className="px-4 pb-4 pt-1 border-t border-gray-800 space-y-3 text-xs"
        >
          {source.brand_name && (
            <div>
              <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Brand</div>
              <div className="text-gray-200 text-sm font-medium">{source.brand_name}</div>
            </div>
          )}
          <div>
            <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Title</div>
            <div className="text-gray-200">{source.title}</div>
          </div>
          <div>
            <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Category</div>
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${categoryClass}`}>
              {categoryLabel}
            </span>
          </div>
          {source.summary && (
            <div>
              <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Summary</div>
              <div className="text-gray-300 whitespace-pre-wrap">{source.summary}</div>
            </div>
          )}
          {source.tags && source.tags.length > 0 && (
            <div>
              <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-1">Tags</div>
              <div className="flex flex-wrap gap-1">
                {source.tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 text-xs border border-gray-700"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
          {source.source_filename && (
            <div>
              <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Source File</div>
              <div className="text-gray-300 break-all">{source.source_filename}</div>
            </div>
          )}
          {source.source_path && (
            <div>
              <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Source Path</div>
              <div className="text-gray-300 break-all">{source.source_path}</div>
            </div>
          )}
          <div>
            <div className="text-gray-500 uppercase tracking-wide text-[10px] mb-0.5">Created</div>
            <div className="text-gray-400">
              {new Date(source.created_at).toLocaleString()}
            </div>
          </div>
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-800">
            <span className="text-[10px] text-gray-600">
              id #{source.id}
              {source.extractor_version ? ` · ${source.extractor_version}` : ""}
            </span>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              Collapse
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || loading) return;

    const userMessage = query.trim();
    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setLoading(true);

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: userMessage }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Failed to get answer");
      }
      if (!data.answer) {
        throw new Error("Empty response from API");
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.answer,
          sources: Array.isArray(data.sources) ? data.sources : [],
          queryMode: data.query_mode,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex flex-col h-screen max-w-4xl mx-auto">
      {/* Header */}
      <div className="px-6 py-8 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Bid IQ</h1>
          <p className="text-gray-400 mt-1">
            Liftnow Knowledge Base — Ask anything about products, specs,
            installation, and more
          </p>
        </div>
        <Link
          href="/knowledge-base"
          className="text-sm text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 px-4 py-2 rounded-lg transition-colors"
        >
          Manage Knowledge Base
        </Link>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 mt-20">
            <p className="text-lg mb-6">Try asking something like:</p>
            <div className="space-y-3 max-w-md mx-auto">
              {[
                "What are the slab requirements for a two-post lift?",
                "Compare the 50-32-F and 75-35-S models",
                "What capacity options are available for parallelogram lifts?",
                "Which lifts in our catalog are ALI-certified?",
                "What are the pit dimensions for a flush mount 75-42?",
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    setQuery(suggestion);
                  }}
                  className="block w-full text-left px-4 py-3 rounded-lg border border-gray-800 hover:border-gray-600 hover:bg-gray-900 transition-colors text-sm text-gray-300"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] ${msg.role === "user" ? "" : "w-full"}`}
            >
              <div
                className={`rounded-xl px-5 py-3 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-200 border border-gray-700"
                }`}
              >
                <div className="whitespace-pre-wrap">{msg.content}</div>
              </div>

              {msg.role === "assistant" && msg.sources && msg.sources.length > 0 && (
                <div className="mt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Sources ({msg.sources.length})
                    </div>
                    {msg.queryMode && (
                      <div className="text-[10px] text-gray-600 font-mono">
                        {msg.queryMode}
                      </div>
                    )}
                  </div>
                  <div className="space-y-2">
                    {msg.sources.map((src, sIdx) => (
                      <SourceCard
                        key={src.id}
                        source={src}
                        messageIdx={i}
                        sourceIdx={sIdx}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-xl px-5 py-3 text-sm text-gray-400 flex items-center gap-2">
              <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full" />
              Searching knowledge base...
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="px-6 py-4 border-t border-gray-800">
        <form onSubmit={handleSubmit} className="flex gap-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask about Liftnow products, specs, installation..."
            className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white font-medium px-6 py-3 rounded-lg transition-colors text-sm"
          >
            Ask
          </button>
        </form>
      </div>
    </main>
  );
}
