"""Tests for dialog classification in marlow.kernel.integration."""

import pytest
from marlow.kernel.integration import DialogType, classify_dialog


class TestClassifyDialog:
    def test_classify_file_exists(self):
        result = classify_dialog("Confirm Save As", "file.txt already exists. Do you want to replace it?", ["Yes", "No"])
        assert result == DialogType.FILE_EXISTS

    def test_classify_file_exists_spanish(self):
        result = classify_dialog("Confirmar", "El archivo ya existe. ¿Desea reemplazar?", ["Sí", "No"])
        assert result == DialogType.FILE_EXISTS

    def test_classify_path_error(self):
        result = classify_dialog("Error", "The path does not exist. Check the path and try again.", ["OK"])
        assert result == DialogType.PATH_ERROR

    def test_classify_path_error_access_denied(self):
        result = classify_dialog("Error", "Access denied to C:\\Windows\\System32", ["OK"])
        assert result == DialogType.PATH_ERROR

    def test_classify_error(self):
        result = classify_dialog("Error", "An unexpected error occurred.", ["OK"])
        assert result == DialogType.ERROR

    def test_classify_error_by_message(self):
        result = classify_dialog("Application", "Operation failed due to timeout.", ["OK"])
        assert result == DialogType.ERROR

    def test_classify_warning(self):
        result = classify_dialog("Warning", "This action may cause data loss.", ["OK", "Cancel"])
        assert result == DialogType.WARNING

    def test_classify_confirmation(self):
        result = classify_dialog("Confirm", "Are you sure you want to delete this file?", ["Yes", "No"])
        assert result == DialogType.CONFIRMATION

    def test_classify_confirmation_spanish(self):
        result = classify_dialog("Confirmar", "¿Desea continuar con la operación?", ["Sí", "No"])
        assert result == DialogType.CONFIRMATION

    def test_classify_save_dialog(self):
        result = classify_dialog("Save As", "", ["Save", "Cancel"])
        assert result == DialogType.SAVE

    def test_classify_open_dialog(self):
        result = classify_dialog("Open File", "", ["Open", "Cancel"])
        assert result == DialogType.OPEN

    def test_classify_information(self):
        result = classify_dialog("Information", "Operation completed successfully.", ["OK"])
        assert result == DialogType.INFORMATION

    def test_classify_unknown(self):
        result = classify_dialog("My Custom Dialog", "Something happened.", ["OK"])
        assert result == DialogType.UNKNOWN

    def test_file_exists_takes_priority_over_error(self):
        """File exists in message should win even if title says error."""
        result = classify_dialog("Error", "file.txt already exists", ["Yes", "No"])
        assert result == DialogType.FILE_EXISTS

    def test_path_error_takes_priority_over_error_title(self):
        """Path not found in message should win over generic error title."""
        result = classify_dialog("Error", "File not found", ["OK"])
        assert result == DialogType.PATH_ERROR
