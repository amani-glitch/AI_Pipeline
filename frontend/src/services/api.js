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

export default api;
