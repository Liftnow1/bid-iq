"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";

interface KnowledgeItem {
  id: number;
  title: string;
  // TEXT[] of v2 3-tier access-model values (single-element under v2).
  category: string[];
  subcategory: string | null;
  tags: string[];
  content_type: string;
  source: string;
  source_filename: string | null;
  summary: string;
  created_at: string;
}

/** First tag in the array (for legacy single-tag UI affordances). */
function primaryCategory(item: KnowledgeItem): string {
  return Array.isArray(item.category) && item.category.length > 0
    ? item.category[0]
    : "uncategorized";
}

interface CategoryCount {
  category: string;
  count: number;
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
  "competitive-intelligence":
    "bg-red-500/20 text-red-300 border-red-500/30",
  "pricing-data": "bg-green-500/20 text-green-300 border-green-500/30",
  "bid-history": "bg-purple-500/20 text-purple-300 border-purple-500/30",
  "installation-guides":
    "bg-orange-500/20 text-orange-300 border-orange-500/30",
  "manufacturer-info": "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  "service-procedures":
    "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  "compliance-certifications":
    "bg-pink-500/20 text-pink-300 border-pink-500/30",
  "customer-intelligence":
    "bg-indigo-500/20 text-indigo-300 border-indigo-500/30",
  general: "bg-gray-500/20 text-gray-300 border-gray-500/30",
};

type InputMode = "type" | "paste" | "upload";

export default function KnowledgeBase() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [categories, setCategories] = useState<CategoryCount[]>([]);
  const [total, setTotal] = useState(0);
  const [filterCategory, setFilterCategory] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const [inputMode, setInputMode] = useState<InputMode>("type");
  const [textContent, setTextContent] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [ingesting, setIngesting] = useState(false);
  const [ingestResult, setIngestResult] = useState<{
    success: boolean;
    message: string;
    classification?: { category: string; summary: string; tags: string[] };
  } | null>(null);

  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchItems = useCallback(async () => {
    const params = new URLSearchParams();
    if (filterCategory) params.set("category", filterCategory);
    if (searchQuery.trim()) params.set("search", searchQuery.trim());

    try {
      const res = await fetch(`/api/knowledge-base/items?${params}`);
      const data = await res.json();
      if (res.ok) {
        setItems(data.items || []);
        setCategories(data.categories || []);
        setTotal(data.total || 0);
      }
    } catch {
      // silently fail on fetch errors
    }
  }, [filterCategory, searchQuery]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const handleTextSubmit = async () => {
    if (!textContent.trim() || ingesting) return;
    setIngesting(true);
    setIngestResult(null);

    try {
      const res = await fetch("/api/knowledge-base/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: textContent,
          source: inputMode === "paste" ? "pasted" : "typed",
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to ingest");

      setIngestResult({
        success: true,
        message: `Classified as "${CATEGORY_LABELS[data.classification.category] || data.classification.category}"`,
        classification: data.classification,
      });
      setTextContent("");
      fetchItems();
    } catch (err) {
      setIngestResult({
        success: false,
        message: err instanceof Error ? err.message : "Something went wrong",
      });
    } finally {
      setIngesting(false);
    }
  };

  const handleFileUpload = async () => {
    if (selectedFiles.length === 0 || ingesting) return;
    setIngesting(true);
    setIngestResult(null);

    try {
      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("source", "uploaded");

        const res = await fetch("/api/knowledge-base/ingest", {
          method: "POST",
          body: formData,
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || `Failed to ingest ${file.name}`);

        setIngestResult({
          success: true,
          message: `${file.name} classified as "${CATEGORY_LABELS[data.classification.category] || data.classification.category}"`,
          classification: data.classification,
        });
      }

      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      fetchItems();
    } catch (err) {
      setIngestResult({
        success: false,
        message: err instanceof Error ? err.message : "Something went wrong",
      });
    } finally {
      setIngesting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      const res = await fetch("/api/knowledge-base/items", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      if (res.ok) fetchItems();
    } catch {
      // silently fail
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      setSelectedFiles(files);
      setInputMode("upload");
    }
  };

  return (
    <main className="flex flex-col h-screen max-w-6xl mx-auto">
      {/* Header */}
      <div className="px-6 py-6 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Knowledge Base</h1>
          <p className="text-gray-400 mt-1">
            Add, classify, and manage your bid intelligence
          </p>
        </div>
        <Link
          href="/"
          className="text-sm text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 px-4 py-2 rounded-lg transition-colors"
        >
          Back to Chat
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="px-6 py-6 space-y-6">
          {/* Input Section */}
          <div className="border border-gray-800 rounded-xl p-6">
            <h2 className="text-lg font-semibold mb-4">Add Knowledge</h2>

            {/* Mode Tabs */}
            <div className="flex gap-2 mb-4">
              {(
                [
                  { key: "type", label: "Type" },
                  { key: "paste", label: "Paste" },
                  { key: "upload", label: "Upload File" },
                ] as { key: InputMode; label: string }[]
              ).map((mode) => (
                <button
                  key={mode.key}
                  onClick={() => {
                    setInputMode(mode.key);
                    setIngestResult(null);
                  }}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    inputMode === mode.key
                      ? "bg-blue-600 text-white"
                      : "bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>

            {/* Text Input (type or paste) */}
            {(inputMode === "type" || inputMode === "paste") && (
              <div>
                <textarea
                  value={textContent}
                  onChange={(e) => setTextContent(e.target.value)}
                  placeholder={
                    inputMode === "type"
                      ? "Type your knowledge here... (product specs, pricing info, competitive intel, bid notes, etc.)"
                      : "Paste content here... (price sheets, spec data, bid results, customer notes, etc.)"
                  }
                  className="w-full h-48 bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors resize-y"
                  disabled={ingesting}
                />
                <div className="flex justify-between items-center mt-3">
                  <span className="text-xs text-gray-500">
                    {textContent.length > 0
                      ? `${textContent.length.toLocaleString()} characters`
                      : "Content will be auto-classified and stored"}
                  </span>
                  <button
                    onClick={handleTextSubmit}
                    disabled={ingesting || !textContent.trim()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white font-medium px-6 py-2.5 rounded-lg transition-colors text-sm flex items-center gap-2"
                  >
                    {ingesting && (
                      <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                    )}
                    {ingesting ? "Classifying..." : "Add to Knowledge Base"}
                  </button>
                </div>
              </div>
            )}

            {/* File Upload */}
            {inputMode === "upload" && (
              <div>
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors ${
                    dragOver
                      ? "border-blue-500 bg-blue-500/10"
                      : "border-gray-700 hover:border-gray-500 hover:bg-gray-800/50"
                  }`}
                >
                  <div className="text-4xl mb-3 text-gray-500">
                    {dragOver ? "+" : "^"}
                  </div>
                  <p className="text-gray-300 mb-1">
                    Drag & drop files here or click to browse
                  </p>
                  <p className="text-xs text-gray-500">
                    Supports PDF, TXT, CSV, JSON, MD files
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.txt,.csv,.json,.md,.doc,.docx"
                    onChange={(e) => {
                      if (e.target.files)
                        setSelectedFiles(Array.from(e.target.files));
                    }}
                    className="hidden"
                  />
                </div>

                {selectedFiles.length > 0 && (
                  <div className="mt-3 space-y-2">
                    {selectedFiles.map((file, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between bg-gray-800 rounded-lg px-4 py-2 text-sm"
                      >
                        <span className="text-gray-300 truncate">
                          {file.name}{" "}
                          <span className="text-gray-500">
                            ({(file.size / 1024).toFixed(1)} KB)
                          </span>
                        </span>
                        <button
                          onClick={() =>
                            setSelectedFiles((f) =>
                              f.filter((_, idx) => idx !== i)
                            )
                          }
                          className="text-gray-500 hover:text-red-400 ml-3"
                        >
                          x
                        </button>
                      </div>
                    ))}
                    <div className="flex justify-end mt-3">
                      <button
                        onClick={handleFileUpload}
                        disabled={ingesting}
                        className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white font-medium px-6 py-2.5 rounded-lg transition-colors text-sm flex items-center gap-2"
                      >
                        {ingesting && (
                          <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                        )}
                        {ingesting
                          ? "Processing..."
                          : `Upload & Classify ${selectedFiles.length} file${selectedFiles.length > 1 ? "s" : ""}`}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Result Banner */}
            {ingestResult && (
              <div
                className={`mt-4 rounded-lg px-4 py-3 text-sm ${
                  ingestResult.success
                    ? "bg-green-500/15 text-green-300 border border-green-500/30"
                    : "bg-red-500/15 text-red-300 border border-red-500/30"
                }`}
              >
                <p className="font-medium">
                  {ingestResult.success ? "Added successfully" : "Error"}
                </p>
                <p className="mt-1">{ingestResult.message}</p>
                {ingestResult.classification && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {ingestResult.classification.tags?.map((tag) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 rounded-full bg-gray-800 text-gray-400 text-xs border border-gray-700"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Browse Section */}
          <div className="border border-gray-800 rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">
                Knowledge Items{" "}
                <span className="text-gray-500 font-normal text-sm">
                  ({total})
                </span>
              </h2>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search knowledge base..."
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 w-64 transition-colors"
              />
            </div>

            {/* Category Filters */}
            {categories.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-4">
                <button
                  onClick={() => setFilterCategory(null)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                    !filterCategory
                      ? "bg-blue-600 text-white border-blue-500"
                      : "bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-500"
                  }`}
                >
                  All ({total})
                </button>
                {categories.map((cat) => (
                  <button
                    key={cat.category}
                    onClick={() =>
                      setFilterCategory(
                        filterCategory === cat.category ? null : cat.category
                      )
                    }
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                      filterCategory === cat.category
                        ? "bg-blue-600 text-white border-blue-500"
                        : CATEGORY_COLORS[cat.category] ||
                          "bg-gray-800 text-gray-400 border-gray-700"
                    }`}
                  >
                    {CATEGORY_LABELS[cat.category] || cat.category} (
                    {cat.count})
                  </button>
                ))}
              </div>
            )}

            {/* Items List */}
            {items.length === 0 ? (
              <div className="text-center py-12 text-gray-500">
                <p className="text-lg mb-2">No knowledge items yet</p>
                <p className="text-sm">
                  Start adding content above to build your knowledge base
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {items.map((item) => (
                  <div
                    key={item.id}
                    className="bg-gray-800/50 border border-gray-700 rounded-lg px-5 py-4 hover:border-gray-600 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          {(Array.isArray(item.category) ? item.category : [primaryCategory(item)]).map((cat) => (
                            <span
                              key={cat}
                              className={`px-2 py-0.5 rounded text-xs font-medium border ${
                                CATEGORY_COLORS[cat] || CATEGORY_COLORS.general
                              }`}
                            >
                              {CATEGORY_LABELS[cat] || cat}
                            </span>
                          ))}
                          {item.subcategory && (
                            <span className="text-xs text-gray-500">
                              / {item.subcategory}
                            </span>
                          )}
                          <span className="text-xs text-gray-600">
                            {item.source === "uploaded"
                              ? `Uploaded: ${item.source_filename || "file"}`
                              : item.source === "pasted"
                                ? "Pasted"
                                : "Typed"}
                          </span>
                        </div>
                        <h3 className="text-sm font-medium text-gray-200 truncate">
                          {item.title}
                        </h3>
                        {item.summary && (
                          <p className="text-xs text-gray-400 mt-1 line-clamp-2">
                            {item.summary}
                          </p>
                        )}
                        {item.tags && item.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {item.tags.slice(0, 5).map((tag) => (
                              <span
                                key={tag}
                                className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-500 text-xs border border-gray-700"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-xs text-gray-600">
                          {new Date(item.created_at).toLocaleDateString()}
                        </span>
                        <button
                          onClick={() => handleDelete(item.id)}
                          className="text-gray-600 hover:text-red-400 text-xs transition-colors"
                          title="Delete"
                        >
                          delete
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
