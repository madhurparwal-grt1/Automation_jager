#!/usr/bin/env bash
#
# Installation script for Automation_jager
# 
# This script installs all necessary dependencies for running the automation scripts:
# - Docker
# - Docker Buildx
# - Python 3.8+
# - GitHub CLI (gh) - optional but recommended
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# Or run with sudo for system-wide installation:
#   sudo ./install.sh

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            DISTRO=$ID
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    else
        log_error "Unsupported operating system: $OSTYPE"
        exit 1
    fi
    log_info "Detected OS: $OS"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check Python installation
check_python() {
    log_info "Checking Python installation..."
    
    if command_exists python3; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)
        
        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
            log_success "Python $PYTHON_VERSION is installed"
            return 0
        else
            log_warning "Python $PYTHON_VERSION found, but 3.8+ is recommended"
            return 1
        fi
    else
        log_error "Python 3 is not installed"
        return 1
    fi
}

# Install Python
install_python() {
    log_info "Installing Python 3..."
    
    if [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install python3
        else
            log_error "Homebrew not found. Please install Python 3.8+ manually from https://www.python.org/downloads/"
            exit 1
        fi
    elif [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "ubuntu" ] || [ "$DISTRO" == "debian" ]; then
            sudo apt-get update
            sudo apt-get install -y python3 python3-pip python3-venv
        elif [ "$DISTRO" == "fedora" ] || [ "$DISTRO" == "rhel" ] || [ "$DISTRO" == "centos" ]; then
            sudo dnf install -y python3 python3-pip
        else
            log_error "Unsupported Linux distribution. Please install Python 3.8+ manually."
            exit 1
        fi
    fi
    
    log_success "Python installed successfully"
}

# Check Docker installation
check_docker() {
    log_info "Checking Docker installation..."
    
    if command_exists docker; then
        DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | tr -d ',')
        log_success "Docker $DOCKER_VERSION is installed"
        
        # Check if Docker daemon is running
        if docker info >/dev/null 2>&1; then
            log_success "Docker daemon is running"
            return 0
        else
            log_warning "Docker is installed but daemon is not running"
            log_info "Please start Docker and run this script again"
            return 1
        fi
    else
        log_error "Docker is not installed"
        return 1
    fi
}

# Install Docker
install_docker() {
    log_info "Installing Docker..."
    
    if [ "$OS" == "macos" ]; then
        log_info "Please install Docker Desktop for Mac from:"
        log_info "https://www.docker.com/products/docker-desktop/"
        log_warning "After installation, start Docker Desktop and run this script again"
        exit 0
    elif [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "ubuntu" ] || [ "$DISTRO" == "debian" ]; then
            # Install Docker using official repository
            sudo apt-get update
            sudo apt-get install -y \
                ca-certificates \
                curl \
                gnupg \
                lsb-release
            
            # Add Docker's official GPG key
            sudo mkdir -p /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$DISTRO/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            
            # Set up repository
            echo \
                "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$DISTRO \
                $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            
            # Install Docker Engine
            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            
            # Add user to docker group
            sudo usermod -aG docker $USER
            log_warning "You need to log out and back in for group changes to take effect"
            
        elif [ "$DISTRO" == "fedora" ] || [ "$DISTRO" == "rhel" ] || [ "$DISTRO" == "centos" ]; then
            sudo dnf -y install dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            
            # Start Docker
            sudo systemctl start docker
            sudo systemctl enable docker
            
            # Add user to docker group
            sudo usermod -aG docker $USER
            log_warning "You need to log out and back in for group changes to take effect"
        else
            log_error "Unsupported Linux distribution for automatic Docker installation"
            log_info "Please install Docker manually from https://docs.docker.com/engine/install/"
            exit 1
        fi
        
        log_success "Docker installed successfully"
    fi
}

# Check Docker Buildx
check_buildx() {
    log_info "Checking Docker Buildx..."
    
    if docker buildx version >/dev/null 2>&1; then
        BUILDX_VERSION=$(docker buildx version | cut -d' ' -f2)
        log_success "Docker Buildx $BUILDX_VERSION is installed"
        return 0
    else
        log_error "Docker Buildx is not installed"
        return 1
    fi
}

# Install Docker Buildx
install_buildx() {
    log_info "Installing Docker Buildx..."
    
    if [ "$OS" == "macos" ]; then
        log_info "Buildx comes with Docker Desktop for Mac"
        log_info "If not available, update Docker Desktop to the latest version"
    elif [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "ubuntu" ] || [ "$DISTRO" == "debian" ]; then
            sudo apt-get update
            sudo apt-get install -y docker-buildx-plugin
        elif [ "$DISTRO" == "fedora" ] || [ "$DISTRO" == "rhel" ] || [ "$DISTRO" == "centos" ]; then
            # Usually included in docker-ce installation
            log_info "Buildx should be included in Docker installation"
        fi
    fi
    
    log_success "Docker Buildx installed successfully"
}

# Check GitHub CLI (optional)
check_gh_cli() {
    log_info "Checking GitHub CLI (optional but recommended)..."
    
    if command_exists gh; then
        GH_VERSION=$(gh --version | head -n1 | cut -d' ' -f3)
        log_success "GitHub CLI $GH_VERSION is installed"
        return 0
    else
        log_warning "GitHub CLI is not installed (optional)"
        return 1
    fi
}

# Install GitHub CLI
install_gh_cli() {
    log_info "Installing GitHub CLI..."
    
    if [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install gh
        else
            log_warning "Homebrew not found. Skipping GitHub CLI installation."
            return 0
        fi
    elif [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "ubuntu" ] || [ "$DISTRO" == "debian" ]; then
            curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
            sudo apt-get update
            sudo apt-get install -y gh
        elif [ "$DISTRO" == "fedora" ] || [ "$DISTRO" == "rhel" ] || [ "$DISTRO" == "centos" ]; then
            sudo dnf install -y 'dnf-command(config-manager)'
            sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
            sudo dnf install -y gh
        fi
    fi
    
    log_success "GitHub CLI installed successfully"
}

# Install Python dependencies
install_python_deps() {
    log_info "Installing Python dependencies..."
    
    if [ -f "requirements.txt" ]; then
        python3 -m pip install --upgrade pip
        python3 -m pip install -r requirements.txt
        log_success "Python dependencies installed"
    else
        log_info "No requirements.txt found (project uses standard library only)"
    fi
}

# Main installation flow
main() {
    echo ""
    log_info "===================================================="
    log_info "  Automation_jager Installation Script"
    log_info "===================================================="
    echo ""
    
    # Detect OS
    detect_os
    echo ""
    
    # Check and install Python
    if ! check_python; then
        read -p "Install Python 3? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_python
        else
            log_error "Python 3.8+ is required. Exiting."
            exit 1
        fi
    fi
    echo ""
    
    # Check and install Docker
    if ! check_docker; then
        read -p "Install Docker? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_docker
        else
            log_error "Docker is required. Exiting."
            exit 1
        fi
    fi
    echo ""
    
    # Check and install Docker Buildx
    if ! check_buildx; then
        read -p "Install Docker Buildx? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_buildx
        else
            log_warning "Docker Buildx is required for multi-architecture builds"
        fi
    fi
    echo ""
    
    # Check and install GitHub CLI (optional)
    if ! check_gh_cli; then
        read -p "Install GitHub CLI? (optional but recommended) (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_gh_cli
        else
            log_info "Skipping GitHub CLI installation"
        fi
    fi
    echo ""
    
    # Install Python dependencies
    install_python_deps
    echo ""
    
    # Final summary
    log_info "===================================================="
    log_success "Installation completed successfully!"
    log_info "===================================================="
    echo ""
    log_info "Next steps:"
    log_info "1. If you installed Docker, you may need to log out and back in"
    log_info "2. Verify installation by running: docker --version && docker buildx version"
    log_info "3. Start using the scripts:"
    log_info "   - Build universal image: python3 build_universal_image.py --help"
    log_info "   - Run orchestrator: python3 -m automation_script.main_orchestrator --help"
    echo ""
    log_info "For more information, see README.md"
    echo ""
}

# Run main function
main
