#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа: python tools/science-search/science.py <команда> ..."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from science_search.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
