import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Loader, Rocket } from "lucide-react";
import UploadZone from "./UploadZone";
import { deployWebsite } from "../services/api";

/**
 * Full deploy page: UploadZone + deployment configuration form.
 */
export default function DeploymentForm() {
  const navigate = useNavigate();

  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("demo");
  const [websiteName, setWebsiteName] = useState("");
  const [domain, setDomain] = useState("");
  const [notificationEmails, setNotificationEmails] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Auto-slugify website name: lowercase, replace spaces/underscores with hyphens,
  // remove non-alphanumeric characters (except hyphens), collapse multiple hyphens.
  const slugify = (value) =>
    value
      .toLowerCase()
      .replace(/[\s_]+/g, "-")
      .replace(/[^a-z0-9-]/g, "")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");

  const handleWebsiteNameChange = useCallback((e) => {
    setWebsiteName(slugify(e.target.value));
  }, []);

  const canSubmit =
    file &&
    websiteName.trim().length > 0 &&
    (mode !== "prod" || domain.trim().length > 0);

  const handleSubmit = useCallback(
    async (e) => {
      e.preventDefault();
      if (!canSubmit || submitting) return;

      setSubmitting(true);
      setError(null);

      try {
        const formData = new FormData();
        formData.append("zip_file", file);
        formData.append("mode", mode);
        formData.append("website_name", websiteName.trim());

        if (mode === "prod" && domain.trim()) {
          formData.append("domain", domain.trim());
        }

        if (notificationEmails.trim()) {
          formData.append("notification_emails", notificationEmails.trim());
        }

        const data = await deployWebsite(formData);
        navigate(`/deployments/${data.deployment_id}`);
      } catch (err) {
        const detail =
          err.response?.data?.detail ||
          err.message ||
          "Deployment failed. Please try again.";
        setError(typeof detail === "string" ? detail : JSON.stringify(detail));
      } finally {
        setSubmitting(false);
      }
    },
    [canSubmit, submitting, file, mode, websiteName, domain, notificationEmails, navigate]
  );

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Deploy Website</h1>
        <p className="mt-2 text-gray-600">
          Upload your website zip file and configure the deployment.
        </p>
      </div>

      {/* Upload Zone */}
      <UploadZone onFileSelected={setFile} />

      {/* Configuration form, visible after file selection */}
      {file && (
        <form onSubmit={handleSubmit} className="mt-8 space-y-6">
          {/* Mode selector */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Deployment Mode
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setMode("demo")}
                className={`flex-1 py-3 px-4 rounded-lg text-sm font-semibold border-2 transition-all ${
                  mode === "demo"
                    ? "border-[#2563EB] bg-[#2563EB] text-white shadow-md"
                    : "border-gray-200 bg-white text-gray-700 hover:border-gray-300"
                }`}
              >
                Demo
                <span className="block text-xs font-normal mt-0.5 opacity-80">
                  Preview on a subdomain
                </span>
              </button>
              <button
                type="button"
                disabled
                className="flex-1 py-3 px-4 rounded-lg text-sm font-semibold border-2 border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed opacity-60"
              >
                Production
                <span className="block text-xs font-normal mt-0.5">
                  Coming soon
                </span>
              </button>
              <button
                type="button"
                onClick={() => setMode("cloudrun")}
                className={`flex-1 py-3 px-4 rounded-lg text-sm font-semibold border-2 transition-all ${
                  mode === "cloudrun"
                    ? "border-teal-600 bg-teal-600 text-white shadow-md"
                    : "border-gray-200 bg-white text-gray-700 hover:border-gray-300"
                }`}
              >
                Cloud Run
                <span className="block text-xs font-normal mt-0.5 opacity-80">
                  Deploy any app
                </span>
              </button>
            </div>
          </div>

          {/* Website name */}
          <div>
            <label
              htmlFor="website-name"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Website Name <span className="text-red-500">*</span>
            </label>
            <input
              id="website-name"
              type="text"
              value={websiteName}
              onChange={handleWebsiteNameChange}
              placeholder="my-awesome-site"
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm
                focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:border-transparent
                placeholder-gray-400"
              required
            />
            <p className="mt-1 text-xs text-gray-500">
              Auto-slugified: lowercase, hyphens only.
            </p>
          </div>

          {/* Domain (prod mode only) */}
          {mode === "prod" && (
            <div>
              <label
                htmlFor="domain"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Domain <span className="text-red-500">*</span>
              </label>
              <input
                id="domain"
                type="text"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="client-site.com"
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm
                  focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:border-transparent
                  placeholder-gray-400"
                required
              />
            </div>
          )}

          {/* Notification emails */}
          <div>
            <label
              htmlFor="emails"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Notification Emails
            </label>
            <input
              id="emails"
              type="text"
              value={notificationEmails}
              onChange={(e) => setNotificationEmails(e.target.value)}
              placeholder="alice@example.com, bob@example.com"
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm
                focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:border-transparent
                placeholder-gray-400"
            />
            <p className="mt-1 text-xs text-gray-500">
              Comma-separated email addresses to notify on completion.
            </p>
          </div>

          {/* Error */}
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={!canSubmit || submitting}
            className={`w-full flex items-center justify-center gap-2 py-3 px-6 rounded-lg
              text-base font-semibold text-white transition-all
              ${
                canSubmit && !submitting
                  ? "bg-[#2563EB] hover:bg-blue-700 shadow-md hover:shadow-lg cursor-pointer"
                  : "bg-gray-300 cursor-not-allowed"
              }`}
          >
            {submitting ? (
              <>
                <Loader className="w-5 h-5 animate-spin" />
                Deploying...
              </>
            ) : (
              <>
                <Rocket className="w-5 h-5" />
                Deploy
              </>
            )}
          </button>
        </form>
      )}
    </div>
  );
}
