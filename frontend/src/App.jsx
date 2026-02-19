import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import DeploymentForm from "./components/DeploymentForm";
import DeploymentDetail from "./pages/DeploymentDetail";
import DeploymentHistory from "./components/DeploymentHistory";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        {/* Deploy page: upload zone + configuration form */}
        <Route path="/" element={<DeploymentForm />} />

        {/* Pipeline view: real-time logs + deployment status */}
        <Route path="/deployments/:id" element={<DeploymentDetail />} />

        {/* History: deployment table */}
        <Route path="/history" element={<DeploymentHistory />} />
      </Route>
    </Routes>
  );
}
