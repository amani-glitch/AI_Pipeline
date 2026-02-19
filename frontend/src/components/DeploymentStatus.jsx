import {
  CheckCircle,
  XCircle,
  ExternalLink,
  Globe,
  Clock,
  Tag,
} from "lucide-react";

/**
 * Deployment result banner: shown when deployment reaches a terminal state.
 *
 * @param {{ deployment: object }} props
 *   deployment: { id, status, website_name, mode, url, domain, steps, ai_summary,
 *                 error_message, created_at, completed_at }
 */
export default function DeploymentStatus({ deployment }) {
  if (!deployment) return null;

  const { status } = deployment;
  const isSuccess = status === "success";
  const isFailed = status === "failed";

  if (!isSuccess && !isFailed) return null;

  // Find the step that failed
  const failedStep = deployment.steps?.find((s) => s.status === "failed");

  const formatDate = (dateStr) => {
    if (!dateStr) return "N/A";
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="space-y-4">
      {/* Result banner */}
      <div
        className={`rounded-xl border-2 p-6 ${
          isSuccess
            ? "border-green-200 bg-green-50"
            : "border-red-200 bg-red-50"
        }`}
      >
        <div className="flex items-start gap-4">
          {isSuccess ? (
            <CheckCircle className="w-8 h-8 text-[#16a34a] flex-shrink-0 mt-0.5" />
          ) : (
            <XCircle className="w-8 h-8 text-[#dc2626] flex-shrink-0 mt-0.5" />
          )}

          <div className="flex-1 min-w-0">
            <h2
              className={`text-xl font-bold ${
                isSuccess ? "text-green-800" : "text-red-800"
              }`}
            >
              {isSuccess ? "Deployment Successful!" : "Deployment Failed"}
            </h2>

            {/* Success: show URL */}
            {isSuccess && deployment.url && (
              <a
                href={deployment.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 mt-2 text-[#2563EB] hover:text-blue-700
                  font-medium text-sm transition-colors"
              >
                <ExternalLink className="w-4 h-4" />
                {deployment.url}
              </a>
            )}

            {/* Failure: show which step failed */}
            {isFailed && failedStep && (
              <p className="mt-2 text-sm text-red-700">
                Failed at step:{" "}
                <span className="font-semibold">{failedStep.name}</span>
              </p>
            )}

            {/* Error message */}
            {isFailed && (deployment.error_message || failedStep?.error) && (
              <div className="mt-3 p-3 bg-red-100 rounded-lg border border-red-200">
                <p className="text-sm text-red-800 font-mono whitespace-pre-wrap">
                  {deployment.error_message || failedStep?.error}
                </p>
              </div>
            )}

            {/* AI Summary */}
            {isSuccess && deployment.ai_summary && (
              <div className="mt-4 p-4 bg-white rounded-lg border border-green-200">
                <h3 className="text-sm font-semibold text-gray-700 mb-1">
                  AI Summary
                </h3>
                <p className="text-sm text-gray-600 whitespace-pre-wrap">
                  {deployment.ai_summary}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Metadata card */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">
          Deployment Details
        </h3>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="flex items-start gap-3">
            <Globe className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
            <div>
              <dt className="text-xs text-gray-500 uppercase tracking-wide">
                Website Name
              </dt>
              <dd className="text-sm font-medium text-gray-900">
                {deployment.website_name}
              </dd>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <Tag className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
            <div>
              <dt className="text-xs text-gray-500 uppercase tracking-wide">
                Mode
              </dt>
              <dd className="text-sm font-medium text-gray-900 capitalize">
                {deployment.mode}
              </dd>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <Clock className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
            <div>
              <dt className="text-xs text-gray-500 uppercase tracking-wide">
                Started
              </dt>
              <dd className="text-sm font-medium text-gray-900">
                {formatDate(deployment.created_at)}
              </dd>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <Clock className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
            <div>
              <dt className="text-xs text-gray-500 uppercase tracking-wide">
                Completed
              </dt>
              <dd className="text-sm font-medium text-gray-900">
                {formatDate(deployment.completed_at)}
              </dd>
            </div>
          </div>
        </dl>
      </div>
    </div>
  );
}
