# Changelog

All notable changes to HyperClaw will be documented in this file.

## [1.0.8] - 2026-03-31

### Added
- Production-ready documentation with complete API reference
- TUI command interface with signal handling and smart routing
- Database schema for PostgreSQL with pgvector support
- Cost-optimized model routing (ChatJimmy integration for 100x cheaper simple tasks)
- Enhanced multi-agent coordination system with 36 specialized agents
- Persistent memory architecture with working, episodic, and semantic layers

### Enhanced
- README with comprehensive architecture documentation
- API endpoints for chat, tasks, memory, and cost management
- CLI commands for setup, server management, and memory operations
- Workspace structure with proper configuration files

### Fixed
- Cloud deployment configuration moved to root directory
- Database connection handling and initialization
- Tool result handling and history management
- Telegram import made optional to prevent startup failures

## [1.0.7] - 2026-03-30

### Added
- Major AGI evolution architecture
- 29-agent specialized swarm deployment
- Enhanced integration bridges (Telegram, iMessage, Email)
- GIL Command Center UI with real-time monitoring
- Parallel processing architecture
- Security enhancements and cognitive monitoring
- Complete swarm orchestration system

### Technical
- Cross-entity AI framework for Hyper Nimbus ecosystem
- Advanced context management and tool integration
- Multi-modal file handling system
- Continuous learning and optimization engines

## [1.0.6] - 2026-03-29

### Fixed
- Clean orphaned tool_results from session history
- Improved error handling with better user feedback
- History trimming before reset to prevent memory issues

## [1.0.5] - 2026-03-28

### Fixed
- Enhanced error handling and display
- Better session management and reset procedures
- Improved stability for long-running sessions

## [1.0.3] - 2026-03-27

### Fixed
- Made Telegram import optional to prevent startup failures
- Better handling of missing optional dependencies
- Improved graceful degradation

## [1.0.2] - 2026-03-26

### Added
- Initial multi-agent system
- Basic integration framework
- Core memory management

## [1.0.1] - 2026-03-25

### Added
- Basic chat functionality
- Simple agent system
- Initial configuration setup

## [1.0.0] - 2026-03-24

### Added
- Initial release of HyperClaw
- Core AI assistant functionality
- Basic setup and configuration