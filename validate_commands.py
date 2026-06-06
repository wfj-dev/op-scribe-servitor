#!/usr/bin/env python3
"""Validation script to check for common issues in bot commands without running live."""

import ast
import sys
from pathlib import Path

# Functions that are defined in bot.py and need _b() wrapper in extracted modules
BOT_FUNCTIONS = {
    "check_command_permission",
    "is_allowed_channel",
    "_is_techmarine_or_forgemaster",
    "_resolve_notification_guild",
    "load_aar_data",
    "save_aar_data",
    "parse_aar",
    "validate_aar",
    "_get_rank_emoji",
    "_print_progress",
    "ToggleFormatView",
    "_load_induction_overrides",
    "_save_induction_overrides",
    "_parse_iso8601_to_utc",
    "_get_emoji_by_name",
    "_get_service_studs_announcement",
    "_get_oathsworn_announcement",
    "_get_award_announcement_channel",
    "_get_watch_veteran_announcement",
    "_get_ardent_raider_announcement",
    "_get_apothecarion_medal_announcement",
    "_get_crimson_laurels_announcement",
    "KT_ROLE_CHANNEL_MAP",
    "_extract_killteam_name",
    "_resolve_killteams_for_member",
    "_resolve_killteam_for_member",
    "_get_bearer_rank_and_title",
    "_get_machine_spirit",
    "ALLOWED_KT_ROLE_IDS",
    "ALLOWED_KT_FORUM_PARENT_IDS",
    "DEBUG_MODE",
}

# Modules that were extracted and should use _b() for bot.py functions
EXTRACTED_MODULES = [
    "opscribe/aar_ops.py",
    "opscribe/forge_ops.py",
    "opscribe/roster_ops.py",
    "opscribe/_refactor.py",
]


class CommandValidator(ast.NodeVisitor):
    """AST visitor to find function calls that should use _b()."""

    def __init__(self, filename):
        self.filename = filename
        self.issues = []
        self.in_function = None
        self.defined_functions = set()

    def visit_Module(self, node):
        # First pass: collect all function definitions in this module
        for item in ast.walk(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defined_functions.add(item.name)
        # Second pass: check for issues
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        old_function = self.in_function
        self.in_function = node.name
        self.generic_visit(node)
        self.in_function = old_function

    def visit_AsyncFunctionDef(self, node):
        old_function = self.in_function
        self.in_function = node.name
        self.generic_visit(node)
        self.in_function = old_function

    def visit_Call(self, node):
        # Check if this is a direct call to a function that should use _b()
        # but only if it's not defined in this module
        if isinstance(node.func, ast.Name) and node.func.id in BOT_FUNCTIONS:
            if node.func.id not in self.defined_functions:
                func_name = node.func.id
                location = self.in_function or "<module>"
                self.issues.append(
                    f"{self.filename}:{node.lineno}: "
                    f"Direct call to '{func_name}()' - should use _b(\"{func_name}\")() "
                    f"in function '{location}'"
                )
        self.generic_visit(node)


def validate_module(filepath):
    """Check a module for common issues."""
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=filepath)
        except SyntaxError as e:
            print(f"❌ {filepath}: Syntax error: {e}")
            return False

    validator = CommandValidator(filepath)
    validator.visit(tree)

    if validator.issues:
        print(f"\n⚠️  {filepath}:")
        for issue in validator.issues:
            print(f"  {issue}")
        return False
    return True


def test_imports():
    """Test that all modules can be imported."""
    print("Testing module imports...")
    try:
        from opscribe import bot
        print("✅ opscribe.bot imports successfully")
        
        # Check critical attributes
        if not hasattr(bot, '_main'):
            print("❌ opscribe.bot missing _main function")
            return False
        
        # Try importing extracted modules
        from opscribe import aar_ops  # noqa: F401
        print("✅ opscribe.aar_ops imports successfully")
        
        from opscribe import forge_ops  # noqa: F401
        print("✅ opscribe.forge_ops imports successfully")
        
        from opscribe import roster_ops  # noqa: F401
        print("✅ opscribe.roster_ops imports successfully")
        
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 70)
    print("OP-Scribe Servitor Command Validation")
    print("=" * 70)
    
    all_ok = True
    
    # Test imports first
    print("\n1. Import Validation")
    print("-" * 70)
    if not test_imports():
        all_ok = False
    
    # Validate extracted modules for _b() usage
    print("\n2. Code Pattern Validation")
    print("-" * 70)
    for module_path in EXTRACTED_MODULES:
        if Path(module_path).exists():
            if not validate_module(module_path):
                all_ok = False
        else:
            print(f"⚠️  Module not found: {module_path}")
    
    # Summary
    print("\n" + "=" * 70)
    if all_ok:
        print("✅ All validations passed!")
        print("\nTo run tests:")
        print("  python3 -m pytest tests/ -v")
        return 0
    else:
        print("❌ Some validations failed - see issues above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
