#!/usr/bin/env python3
"""Launch the canonical HTTP app; its lifespan owns all background services."""

from hyperclaw.server import main


if __name__ == "__main__":
    main()
