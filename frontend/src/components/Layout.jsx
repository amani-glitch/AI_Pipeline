import { NavLink, Outlet } from "react-router-dom";
import { Rocket, Upload, History } from "lucide-react";

export default function Layout() {
  const linkClass = ({ isActive }) =>
    `flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? "bg-blue-700 text-white"
        : "text-blue-100 hover:bg-blue-600 hover:text-white"
    }`;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Navigation bar */}
      <nav className="bg-[#2563EB] shadow-lg">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Logo */}
            <NavLink to="/" className="flex items-center gap-2 text-white">
              <Rocket className="w-6 h-6" />
              <span className="text-xl font-bold tracking-tight">
                WebDeploy
              </span>
            </NavLink>

            {/* Navigation links */}
            <div className="flex items-center gap-2">
              <NavLink to="/" end className={linkClass}>
                <Upload className="w-4 h-4" />
                Deploy
              </NavLink>
              <NavLink to="/history" className={linkClass}>
                <History className="w-4 h-4" />
                History
              </NavLink>
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>
    </div>
  );
}
