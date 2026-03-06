"use client";

import { useState, useCallback } from "react";

interface BidAnalysis {
  title: string;
  issuing_agency: string;
  bid_number: string;
  bid_deadline: string;
  qa_deadline: string;
  site_visit: string;
  delivery_method: string;
  key_dates: { date: string; description: string }[];
  scope_summary: string;
  products_requested: string[];
  specifications: string[];
  substitutions_allowed: boolean;
  substitution_details: string;
  required_forms: string[];
  bonds_required: string[];
  insurance_requirements: string;
  licensing_requirements: string;
  evaluation_criteria: string[];
  services_included: string[];
  services_excluded: string[];
  strategic_notes: string;
  risk_assessment: string;
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [analysis, setAnalysis] = useState<BidAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.[0]) {
      setFile(e.dataTransfer.files[0]);
      setAnalysis(null);
      setError(null);
    }
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.[0]) {
      setFile(e.target.files[0]);
      setAnalysis(null);
      setError(null);
    }
  };

  const handleSubmit = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setAnalysis(null);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch("/api/extract-bid", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.error || "Failed to analyze bid");
      }

      const data = await res.json();
      setAnalysis(data.analysis);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="max-w-5xl mx-auto px-6 py-12">
      <div className="mb-12">
        <h1 className="text-4xl font-bold mb-2">Bid IQ</h1>
        <p className="text-gray-400 text-lg">
          Upload a bid document and get instant AI-powered analysis
        </p>
      </div>

      {/* Upload Section */}
      <div
        className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors ${
          dragActive
            ? "border-blue-500 bg-blue-500/10"
            : "border-gray-700 hover:border-gray-500"
        }`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept=".pdf"
          onChange={handleFileChange}
          className="hidden"
          id="file-upload"
        />
        <label htmlFor="file-upload" className="cursor-pointer">
          <div className="text-5xl mb-4">&#128196;</div>
          <p className="text-lg mb-2">
            {file ? file.name : "Drop a bid PDF here or click to upload"}
          </p>
          <p className="text-sm text-gray-500">PDF files up to 32MB</p>
        </label>
      </div>

      {file && (
        <button
          onClick={handleSubmit}
          disabled={loading}
          className="mt-6 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-semibold py-3 px-6 rounded-lg transition-colors"
        >
          {loading ? "Analyzing bid..." : "Analyze Bid"}
        </button>
      )}

      {loading && (
        <div className="mt-8 flex items-center justify-center gap-3 text-gray-400">
          <div className="animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full" />
          Extracting bid data with AI... This may take a minute.
        </div>
      )}

      {error && (
        <div className="mt-6 bg-red-900/30 border border-red-700 rounded-lg p-4 text-red-300">
          {error}
        </div>
      )}

      {/* Results */}
      {analysis && (
        <div className="mt-10 space-y-8">
          {/* Header Info */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h2 className="text-2xl font-bold mb-4">{analysis.title}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <InfoRow label="Issuing Agency" value={analysis.issuing_agency} />
              <InfoRow label="Bid Number" value={analysis.bid_number} />
              <InfoRow label="Bid Deadline" value={analysis.bid_deadline} />
              <InfoRow label="Q&A Deadline" value={analysis.qa_deadline} />
              <InfoRow label="Site Visit" value={analysis.site_visit} />
              <InfoRow label="Delivery Method" value={analysis.delivery_method} />
            </div>
          </section>

          {/* Key Dates */}
          {analysis.key_dates?.length > 0 && (
            <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
              <h3 className="text-lg font-semibold mb-3">Key Dates</h3>
              <div className="space-y-2">
                {analysis.key_dates.map((d, i) => (
                  <div key={i} className="flex gap-4 text-sm">
                    <span className="text-blue-400 font-mono min-w-[120px]">
                      {d.date}
                    </span>
                    <span className="text-gray-300">{d.description}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Scope */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">Scope Summary</h3>
            <p className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">
              {analysis.scope_summary}
            </p>
          </section>

          {/* Products & Specs */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">
              Products & Specifications
            </h3>
            <div className="mb-4">
              <h4 className="text-sm font-medium text-gray-400 mb-2">
                Products Requested
              </h4>
              <ListItems items={analysis.products_requested} />
            </div>
            <div className="mb-4">
              <h4 className="text-sm font-medium text-gray-400 mb-2">
                Specifications
              </h4>
              <ListItems items={analysis.specifications} />
            </div>
            <div className="flex gap-6 text-sm">
              <span className="text-gray-400">Substitutions:</span>
              <span
                className={
                  analysis.substitutions_allowed
                    ? "text-green-400"
                    : "text-red-400"
                }
              >
                {analysis.substitutions_allowed ? "Allowed" : "Not Allowed"}
              </span>
            </div>
            {analysis.substitution_details && (
              <p className="text-sm text-gray-400 mt-1">
                {analysis.substitution_details}
              </p>
            )}
          </section>

          {/* Requirements */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">Requirements</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Required Forms
                </h4>
                <ListItems items={analysis.required_forms} />
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Bonds Required
                </h4>
                <ListItems items={analysis.bonds_required} />
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Insurance
                </h4>
                <p className="text-sm text-gray-300">
                  {analysis.insurance_requirements || "Not specified"}
                </p>
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Licensing
                </h4>
                <p className="text-sm text-gray-300">
                  {analysis.licensing_requirements || "Not specified"}
                </p>
              </div>
            </div>
          </section>

          {/* Evaluation Criteria */}
          {analysis.evaluation_criteria?.length > 0 && (
            <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
              <h3 className="text-lg font-semibold mb-3">
                Evaluation Criteria
              </h3>
              <ListItems items={analysis.evaluation_criteria} />
            </section>
          )}

          {/* Services */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">Services</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Included
                </h4>
                <ListItems items={analysis.services_included} />
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-400 mb-2">
                  Excluded
                </h4>
                <ListItems items={analysis.services_excluded} />
              </div>
            </div>
          </section>

          {/* Strategic Notes */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">Strategic Notes</h3>
            <p className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">
              {analysis.strategic_notes}
            </p>
          </section>

          {/* Risk */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h3 className="text-lg font-semibold mb-3">Risk Assessment</h3>
            <p className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">
              {analysis.risk_assessment}
            </p>
          </section>
        </div>
      )}
    </main>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-gray-500">{label}:</span>{" "}
      <span className="text-gray-200">{value || "Not specified"}</span>
    </div>
  );
}

function ListItems({ items }: { items: string[] }) {
  if (!items || items.length === 0) {
    return <p className="text-sm text-gray-500">None specified</p>;
  }
  return (
    <ul className="space-y-1">
      {items.map((item, i) => (
        <li key={i} className="text-sm text-gray-300 flex gap-2">
          <span className="text-gray-600">-</span>
          {item}
        </li>
      ))}
    </ul>
  );
}
