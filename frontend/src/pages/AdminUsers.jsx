import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Users, CheckCircle, XCircle, Clock, Shield, User, Loader } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { listUsers, approveUser, rejectUser } from "../services/api";

const roleLabels = {
  admin: "Admin",
  super_user: "Super User",
  simple_user: "Simple User",
};

const roleBadgeColors = {
  admin: "bg-purple-100 text-purple-800",
  super_user: "bg-emerald-100 text-emerald-800",
  simple_user: "bg-blue-100 text-blue-800",
};

const statusBadgeColors = {
  approved: "bg-green-100 text-green-800",
  pending: "bg-amber-100 text-amber-800",
  rejected: "bg-red-100 text-red-800",
};

export default function AdminUsers() {
  const { isAdmin, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);

  const fetchUsers = useCallback(async () => {
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      console.error("Failed to fetch users:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!isAdmin) {
      navigate("/", { replace: true });
      return;
    }
    fetchUsers();
  }, [authLoading, isAdmin, navigate, fetchUsers]);

  const handleApprove = async (uid) => {
    setActionLoading(uid);
    try {
      await approveUser(uid);
      await fetchUsers();
    } catch (err) {
      console.error("Failed to approve user:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (uid) => {
    setActionLoading(uid);
    try {
      await rejectUser(uid);
      await fetchUsers();
    } catch (err) {
      console.error("Failed to reject user:", err);
    } finally {
      setActionLoading(null);
    }
  };

  if (authLoading || loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader className="w-6 h-6 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <Users className="w-7 h-7 text-blue-600" />
        <h1 className="text-2xl font-bold text-gray-900">Gestion des Utilisateurs</h1>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left py-3 px-4 text-xs font-semibold text-gray-500 uppercase">Utilisateur</th>
              <th className="text-left py-3 px-4 text-xs font-semibold text-gray-500 uppercase">R&ocirc;le demand&eacute;</th>
              <th className="text-left py-3 px-4 text-xs font-semibold text-gray-500 uppercase">R&ocirc;le</th>
              <th className="text-left py-3 px-4 text-xs font-semibold text-gray-500 uppercase">Statut</th>
              <th className="text-left py-3 px-4 text-xs font-semibold text-gray-500 uppercase">Date</th>
              <th className="text-right py-3 px-4 text-xs font-semibold text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.uid} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="py-3 px-4">
                  <div className="font-medium text-gray-900">{u.display_name || "—"}</div>
                  <div className="text-sm text-gray-500">{u.email}</div>
                </td>
                <td className="py-3 px-4">
                  <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${roleBadgeColors[u.requested_role] || "bg-gray-100 text-gray-800"}`}>
                    {roleLabels[u.requested_role] || u.requested_role}
                  </span>
                </td>
                <td className="py-3 px-4">
                  {u.role ? (
                    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${roleBadgeColors[u.role] || "bg-gray-100 text-gray-800"}`}>
                      {u.role === "admin" && <Shield className="w-3 h-3" />}
                      {roleLabels[u.role] || u.role}
                    </span>
                  ) : (
                    <span className="text-gray-400 text-sm">—</span>
                  )}
                </td>
                <td className="py-3 px-4">
                  <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${statusBadgeColors[u.status] || "bg-gray-100 text-gray-800"}`}>
                    {u.status === "approved" && <CheckCircle className="w-3 h-3" />}
                    {u.status === "pending" && <Clock className="w-3 h-3" />}
                    {u.status === "rejected" && <XCircle className="w-3 h-3" />}
                    {u.status}
                  </span>
                </td>
                <td className="py-3 px-4 text-sm text-gray-500">
                  {u.created_at ? new Date(u.created_at).toLocaleDateString("fr-FR") : "—"}
                </td>
                <td className="py-3 px-4 text-right">
                  {u.status === "pending" && (
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => handleApprove(u.uid)}
                        disabled={actionLoading === u.uid}
                        className="px-3 py-1.5 rounded-md text-xs font-semibold bg-green-50 text-green-700
                          hover:bg-green-100 transition-colors cursor-pointer disabled:opacity-50"
                      >
                        Approuver
                      </button>
                      <button
                        onClick={() => handleReject(u.uid)}
                        disabled={actionLoading === u.uid}
                        className="px-3 py-1.5 rounded-md text-xs font-semibold bg-red-50 text-red-700
                          hover:bg-red-100 transition-colors cursor-pointer disabled:opacity-50"
                      >
                        Rejeter
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={6} className="py-8 text-center text-gray-500">
                  Aucun utilisateur enregistr&eacute;.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
