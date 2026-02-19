import { useEffect, useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  ExternalLink,
  ArrowUpDown,
  Inbox,
  RefreshCw,
  Loader,
} from "lucide-react";
import { getDeployments } from "../services/api";

const STATUS_BADGE = {
  queued: "bg-gray-100 text-gray-700 ring-gray-300",
  running: "bg-blue-100 text-blue-700 ring-blue-300",
  success: "bg-green-100 text-green-700 ring-green-300",
  failed: "bg-red-100 text-red-700 ring-red-300",
};

const MODE_BADGE = {
  demo: "bg-purple-100 text-purple-700 ring-purple-300",
  prod: "bg-orange-100 text-orange-700 ring-orange-300",
  cloudrun: "bg-teal-100 text-teal-700 ring-teal-300",
};

/**
 * Deployment history table with sortable date column, status/mode badges, and clickable rows.
 */
export default function DeploymentHistory() {
  const navigate = useNavigate();
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortAsc, setSortAsc] = useState(false); // default newest first

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDeployments();
      // Normalize: API might return { deployments: [...] } or just an array
      setDeployments(Array.isArray(data) ? data : data.deployments || []);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Failed to load deployments");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const sorted = useMemo(() => {
    const copy = [...deployments];
    copy.sort((a, b) => {
      const dateA = new Date(a.created_at || 0).getTime();
      const dateB = new Date(b.created_at || 0).getTime();
      return sortAsc ? dateA - dateB : dateB - dateA;
    });
    return copy;
  }, [deployments, sortAsc]);

  const formatDate = (dateStr) => {
    if (!dateStr) return "N/A";
    return new Date(dateStr).toLocaleString();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader className="w-8 h-8 text-[#2563EB] animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto text-center py-16">
        <p className="text-red-600 font-medium mb-4">{error}</p>
        <button
          onClick={fetchData}
          className="inline-flex items-center gap-2 px-4 py-2 bg-[#2563EB] text-white
            rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Deployment History</h1>
          <p className="mt-1 text-gray-600">
            {deployments.length} deployment{deployments.length !== 1 ? "s" : ""}
          </p>
        </div>
        <button
          onClick={fetchData}
          className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-200
            rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-gray-400">
          <Inbox className="w-16 h-16 mb-4" />
          <p className="text-lg font-medium text-gray-500">No deployments yet</p>
          <p className="text-sm text-gray-400 mt-1">
            Deploy your first website to see it here.
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-6 py-3 text-left font-semibold text-gray-600">
                    Website Name
                  </th>
                  <th className="px-6 py-3 text-left font-semibold text-gray-600">
                    Mode
                  </th>
                  <th className="px-6 py-3 text-left font-semibold text-gray-600">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left font-semibold text-gray-600">
                    URL
                  </th>
                  <th
                    className="px-6 py-3 text-left font-semibold text-gray-600 cursor-pointer
                      select-none hover:text-[#2563EB] transition-colors"
                    onClick={() => setSortAsc((prev) => !prev)}
                  >
                    <span className="inline-flex items-center gap-1">
                      Date
                      <ArrowUpDown className="w-3.5 h-3.5" />
                    </span>
                  </th>
                  <th className="px-6 py-3 text-left font-semibold text-gray-600">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sorted.map((dep) => (
                  <tr
                    key={dep.id}
                    onClick={() => navigate(`/deployments/${dep.id}`)}
                    className="hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td className="px-6 py-4 font-medium text-gray-900">
                      {dep.website_name}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs
                          font-medium ring-1 ring-inset capitalize
                          ${MODE_BADGE[dep.mode] || MODE_BADGE.demo}`}
                      >
                        {dep.mode}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs
                          font-medium ring-1 ring-inset capitalize
                          ${STATUS_BADGE[dep.status] || STATUS_BADGE.queued}`}
                      >
                        {dep.status}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      {dep.result_url ? (
                        <a
                          href={dep.result_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center gap-1 text-[#2563EB] hover:text-blue-700
                            font-medium transition-colors"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                          <span className="truncate max-w-[200px]">{dep.result_url}</span>
                        </a>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-gray-500 whitespace-nowrap">
                      {formatDate(dep.created_at)}
                    </td>
                    <td className="px-6 py-4">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/deployments/${dep.id}`);
                        }}
                        className="text-[#2563EB] hover:text-blue-700 text-xs font-medium
                          transition-colors"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
