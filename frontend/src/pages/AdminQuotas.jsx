import { useState, useEffect, useCallback } from "react";
import { Loader2, Save, CheckCircle, AlertCircle, Gauge, Users } from "lucide-react";
import { getQuotaDefaults, setRoleQuota, listUsers, getUserQuota, setUserQuota } from "../services/api";

const ROLE_LABELS = {
  simple_user: "Utilisateur Simple",
  super_user: "Super Utilisateur",
  admin: "Administrateur",
};

const FIELD_LABELS = {
  max_deployments_per_day: "Deploiements / jour",
  max_zip_size_mb: "Taille ZIP max (MB)",
  max_concurrent_deployments: "Deploiements simultanes",
  max_total_deployments: "Total deploiements (-1 = illimite)",
};

export default function AdminQuotas() {
  const [roleQuotas, setRoleQuotas] = useState({});
  const [users, setUsers] = useState([]);
  const [selectedUser, setSelectedUser] = useState(null);
  const [userQuota, setUserQuotaState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(null);
  const [message, setMessage] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [defaults, userList] = await Promise.all([
        getQuotaDefaults(),
        listUsers(),
      ]);
      setRoleQuotas(defaults);
      setUsers(userList.filter((u) => u.status === "approved"));
    } catch {
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleRoleChange = (role, field, value) => {
    setRoleQuotas((prev) => ({
      ...prev,
      [role]: { ...prev[role], [field]: parseInt(value) || 0 },
    }));
    setMessage(null);
  };

  const handleSaveRole = async (role) => {
    setSaving(role);
    setMessage(null);
    try {
      await setRoleQuota(role, roleQuotas[role]);
      setMessage({ type: "success", text: `Quotas ${ROLE_LABELS[role]} enregistres.` });
    } catch {
      setMessage({ type: "error", text: "Erreur lors de la sauvegarde." });
    } finally {
      setSaving(null);
    }
  };

  const handleSelectUser = async (uid) => {
    setSelectedUser(uid);
    setUserQuotaState(null);
    try {
      const data = await getUserQuota(uid);
      setUserQuotaState(data.config);
    } catch {}
  };

  const handleUserQuotaChange = (field, value) => {
    setUserQuotaState((prev) => ({
      ...prev,
      [field]: parseInt(value) || 0,
    }));
    setMessage(null);
  };

  const handleSaveUserQuota = async () => {
    if (!selectedUser || !userQuota) return;
    setSaving("user");
    setMessage(null);
    try {
      await setUserQuota(selectedUser, userQuota);
      setMessage({ type: "success", text: "Quotas utilisateur enregistres." });
    } catch {
      setMessage({ type: "error", text: "Erreur lors de la sauvegarde." });
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Gestion des Quotas</h1>
        <p className="text-sm text-gray-500 mt-1">
          Definir les limites de deploiement par role et par utilisateur
        </p>
      </div>

      {message && (
        <div
          className={`flex items-center gap-2 p-3 rounded-lg text-sm ${
            message.type === "success"
              ? "bg-green-50 text-green-700 border border-green-200"
              : "bg-red-50 text-red-700 border border-red-200"
          }`}
        >
          {message.type === "success" ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {message.text}
        </div>
      )}

      {/* Role quotas */}
      <div className="space-y-4">
        <h2 className="font-semibold text-gray-900 flex items-center gap-2">
          <Gauge className="w-5 h-5 text-blue-600" />
          Quotas par role
        </h2>

        {Object.entries(ROLE_LABELS).map(([role, label]) => (
          <div key={role} className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-gray-900">{label}</h3>
              <button
                onClick={() => handleSaveRole(role)}
                disabled={saving === role}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-sm rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
              >
                {saving === role ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                Enregistrer
              </button>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {Object.entries(FIELD_LABELS).map(([field, fieldLabel]) => (
                <div key={field}>
                  <label className="block text-xs font-medium text-gray-500 mb-1">{fieldLabel}</label>
                  <input
                    type="number"
                    value={roleQuotas[role]?.[field] ?? 0}
                    onChange={(e) => handleRoleChange(role, field, e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Per-user quotas */}
      <div className="space-y-4">
        <h2 className="font-semibold text-gray-900 flex items-center gap-2">
          <Users className="w-5 h-5 text-purple-600" />
          Quotas par utilisateur (override)
        </h2>

        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Selectionner un utilisateur</label>
            <select
              value={selectedUser || ""}
              onChange={(e) => handleSelectUser(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">-- Choisir --</option>
              {users.map((u) => (
                <option key={u.uid} value={u.uid}>
                  {u.display_name || u.email} ({ROLE_LABELS[u.role] || u.role})
                </option>
              ))}
            </select>
          </div>

          {selectedUser && userQuota && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                {Object.entries(FIELD_LABELS).map(([field, fieldLabel]) => (
                  <div key={field}>
                    <label className="block text-xs font-medium text-gray-500 mb-1">{fieldLabel}</label>
                    <input
                      type="number"
                      value={userQuota[field] ?? 0}
                      onChange={(e) => handleUserQuotaChange(field, e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                ))}
              </div>
              <button
                onClick={handleSaveUserQuota}
                disabled={saving === "user"}
                className="flex items-center gap-1.5 px-4 py-2 bg-purple-600 text-white text-sm rounded-lg font-medium hover:bg-purple-700 disabled:opacity-50 cursor-pointer"
              >
                {saving === "user" ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                Enregistrer l'override
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
