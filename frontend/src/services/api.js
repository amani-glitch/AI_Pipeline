import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "",
  headers: {
    Accept: "application/json",
  },
});

/**
 * Deploy a website by uploading a zip file with configuration.
 * @param {FormData} formData - Must include: zip_file, mode, website_name.
 *   Optional: domain, notification_emails.
 * @returns {Promise<object>} The created deployment object.
 */
export async function deployWebsite(formData) {
  const response = await api.post("/api/deploy", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 600000, // 10 minutes — folder uploads can be large
    maxContentLength: Infinity,
    maxBodyLength: Infinity,
  });
  return response.data;
}

/**
 * List all deployments.
 * @returns {Promise<object[]>} Array of deployment objects.
 */
export async function getDeployments() {
  const response = await api.get("/api/deployments");
  return response.data;
}

/**
 * Get a single deployment by ID.
 * @param {string} id - Deployment ID.
 * @returns {Promise<object>} Deployment object with steps and metadata.
 */
export async function getDeployment(id) {
  const response = await api.get(`/api/deployments/${id}`);
  return response.data;
}

/**
 * Get logs for a deployment.
 * @param {string} id - Deployment ID.
 * @returns {Promise<object>} Logs data.
 */
export async function getDeploymentLogs(id) {
  const response = await api.get(`/api/deployments/${id}/logs`);
  return response.data;
}

/**
 * Delete a deployment and clean up its GCP resources.
 * @param {string} id - Deployment ID.
 * @returns {Promise<object>} Deletion result.
 */
export async function deleteDeployment(id) {
  const response = await api.delete(`/api/deployments/${id}`);
  return response.data;
}

/**
 * Fetch pipeline statistics for a date range.
 * @param {object} params - Query params: preset, start_date, end_date.
 * @returns {Promise<object>} Statistics response.
 */
export async function getStatistics(params = {}) {
  const response = await api.get("/api/statistics", { params });
  return response.data;
}

/**
 * Send on-demand report emails for a date range.
 * @param {object} body - { start_date, end_date, send_to_deployers, send_to_admins }
 * @returns {Promise<object>} { emails_sent, recipients }
 */
export async function sendOnDemandReport(body) {
  const response = await api.post("/api/statistics/send-report", body);
  return response.data;
}

/**
 * Check domain availability/ownership via Cloud Domains API.
 * @param {string} domain - Domain name to check (e.g. "example.com").
 * @returns {Promise<object>} { status, price_amount, price_currency, message }
 */
export async function checkDomain(domain) {
  const response = await api.get("/api/domains/check", { params: { domain } });
  return response.data;
}

// ── Auth API ──────────────────────────────────────────────────────────

/**
 * Exchange a Google ID token (from GIS) for a Firebase Custom Token.
 * @param {string} credential - Google ID token from Google Identity Services.
 * @returns {Promise<{custom_token: string}>}
 */
export async function googleSignIn(credential) {
  const response = await api.post("/api/auth/google-signin", { credential });
  return response.data;
}

/**
 * Sign up (create pending user account).
 * @param {string} requestedRole - "simple_user" or "super_user"
 * @returns {Promise<object>} User profile.
 */
export async function signup(requestedRole) {
  const response = await api.post("/api/auth/signup", {
    requested_role: requestedRole,
  });
  return response.data;
}

/**
 * Get the current user's profile.
 * @returns {Promise<object>} User profile.
 */
export async function getMe() {
  const response = await api.get("/api/auth/me");
  return response.data;
}

/**
 * List all users (admin only).
 * @returns {Promise<object[]>} Array of user objects.
 */
export async function listUsers() {
  const response = await api.get("/api/auth/users");
  return response.data;
}

/**
 * Approve a user (admin or email token).
 * @param {string} uid - Firebase UID.
 * @param {string} [token] - Optional approval token from email link.
 * @returns {Promise<object>} Updated user profile.
 */
export async function approveUser(uid, token) {
  const params = token ? { token } : {};
  const response = await api.post(`/api/auth/approve/${uid}`, null, { params });
  return response.data;
}

/**
 * Reject a user (admin or email token).
 * @param {string} uid - Firebase UID.
 * @param {string} [token] - Optional rejection token from email link.
 * @returns {Promise<object>} Updated user profile.
 */
export async function rejectUser(uid, token) {
  const params = token ? { token } : {};
  const response = await api.post(`/api/auth/reject/${uid}`, null, { params });
  return response.data;
}

// ── Notification Preferences API ──────────────────────────────────────

/**
 * Get the current user's notification preferences.
 * @returns {Promise<object>} Notification preferences.
 */
export async function getNotificationPreferences() {
  const response = await api.get("/api/auth/me/notification-preferences");
  return response.data;
}

/**
 * Update the current user's notification preferences.
 * @param {object} prefs - Partial preferences to update.
 * @returns {Promise<object>} Updated notification preferences.
 */
export async function updateNotificationPreferences(prefs) {
  const response = await api.put("/api/auth/me/notification-preferences", prefs);
  return response.data;
}

// ── Preview API ──────────────────────────────────────────────────────

export async function createPreview(formData) {
  const response = await api.post("/api/preview", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
    maxContentLength: Infinity,
    maxBodyLength: Infinity,
  });
  return response.data;
}

export async function deletePreview(previewId) {
  const response = await api.delete(`/api/preview/${previewId}`);
  return response.data;
}

// ── Git Integration API ──────────────────────────────────────────────

export async function getGitConnections() {
  const response = await api.get("/api/git/connections");
  return response.data;
}

export async function createGitConnection(data) {
  const response = await api.post("/api/git/connections", data);
  return response.data;
}

export async function deleteGitConnection(id) {
  const response = await api.delete(`/api/git/connections/${id}`);
  return response.data;
}

export async function updateGitBranch(id, branch) {
  const response = await api.patch(`/api/git/connections/${id}/branch`, { branch });
  return response.data;
}

export async function getGitPushEvents(connectionId) {
  const params = connectionId ? { connection_id: connectionId } : {};
  const response = await api.get("/api/git/push-events", { params });
  return response.data;
}

// ── Admin Dashboard API ──────────────────────────────────────────────

export async function getDashboard() {
  const response = await api.get("/api/admin/dashboard");
  return response.data;
}

// ── Quota API ────────────────────────────────────────────────────────

export async function getQuotaDefaults() {
  const response = await api.get("/api/quotas/defaults");
  return response.data;
}

export async function setRoleQuota(role, config) {
  const response = await api.put(`/api/quotas/role/${role}`, config);
  return response.data;
}

export async function getUserQuota(uid) {
  const response = await api.get(`/api/quotas/user/${uid}`);
  return response.data;
}

export async function setUserQuota(uid, config) {
  const response = await api.put(`/api/quotas/user/${uid}`, config);
  return response.data;
}

export async function getMyQuotaUsage() {
  const response = await api.get("/api/quotas/my-usage");
  return response.data;
}

// ── Alerts API ───────────────────────────────────────────────────────

export async function getAlerts(params = {}) {
  const response = await api.get("/api/alerts", { params });
  return response.data;
}

export async function resolveAlert(id) {
  const response = await api.post(`/api/alerts/${id}/resolve`);
  return response.data;
}

export async function getUnresolvedAlertCount() {
  const response = await api.get("/api/alerts/unresolved-count");
  return response.data;
}

export default api;
