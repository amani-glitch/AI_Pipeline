import { useState, useRef, useCallback } from "react";
import { Upload, FileArchive, X } from "lucide-react";

const MAX_SIZE_MB = 500;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

/**
 * Drag-and-drop zone for .zip file uploads.
 * Accepts only .zip files, max 500MB.
 *
 * @param {{ onFileSelected: (file: File) => void }} props
 */
export default function UploadZone({ onFileSelected }) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const validateFile = useCallback((file) => {
    if (!file) return "No file selected.";
    if (!file.name.toLowerCase().endsWith(".zip") && file.type !== "application/zip") {
      return "Only .zip files are accepted.";
    }
    if (file.size > MAX_SIZE_BYTES) {
      return `File exceeds the ${MAX_SIZE_MB}MB limit.`;
    }
    return null;
  }, []);

  const handleFile = useCallback(
    (file) => {
      const validationError = validateFile(file);
      if (validationError) {
        setError(validationError);
        setSelectedFile(null);
        return;
      }
      setError(null);
      setSelectedFile(file);
      onFileSelected(file);
    },
    [validateFile, onFileSelected]
  );

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);

      const file = e.dataTransfer.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleInputChange = useCallback(
    (e) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      // Reset so the same file can be re-selected
      e.target.value = "";
    },
    [handleFile]
  );

  const handleRemove = useCallback(() => {
    setSelectedFile(null);
    setError(null);
    onFileSelected(null);
  }, [onFileSelected]);

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="w-full">
      {!selectedFile ? (
        <div
          role="button"
          tabIndex={0}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
          className={`relative flex flex-col items-center justify-center gap-4 p-12
            border-2 border-dashed rounded-xl cursor-pointer transition-all duration-200
            ${
              isDragging
                ? "border-[#2563EB] bg-blue-50 scale-[1.01]"
                : "border-gray-300 bg-white hover:border-[#2563EB] hover:bg-gray-50"
            }
            ${error ? "border-red-400 bg-red-50" : ""}`}
        >
          <Upload
            className={`w-12 h-12 ${
              isDragging ? "text-[#2563EB]" : "text-gray-400"
            } transition-colors`}
          />
          <div className="text-center">
            <p className="text-lg font-semibold text-gray-700">
              {isDragging ? "Drop your zip file here" : "Drag & drop your zip file here"}
            </p>
            <p className="mt-1 text-sm text-gray-500">
              or <span className="text-[#2563EB] font-medium">click to browse</span>
            </p>
            <p className="mt-2 text-xs text-gray-400">
              .zip files only, max {MAX_SIZE_MB}MB
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".zip,application/zip"
            onChange={handleInputChange}
            className="hidden"
            aria-label="Upload zip file"
          />
        </div>
      ) : (
        <div className="flex items-center gap-4 p-6 bg-white border border-gray-200 rounded-xl">
          <div className="flex items-center justify-center w-12 h-12 bg-blue-50 rounded-lg">
            <FileArchive className="w-6 h-6 text-[#2563EB]" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">
              {selectedFile.name}
            </p>
            <p className="text-sm text-gray-500">
              {formatSize(selectedFile.size)}
            </p>
          </div>
          <button
            type="button"
            onClick={handleRemove}
            className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors"
            aria-label="Remove file"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      )}

      {error && (
        <p className="mt-2 text-sm text-red-600 font-medium">{error}</p>
      )}
    </div>
  );
}
