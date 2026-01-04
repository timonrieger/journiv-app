#!/bin/bash

# Journiv Backend Deployment Script
# This script helps in deploying the backend in production or development environment.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -e, --env ENVIRONMENT    Set environment (development|production) [default: development]"
    echo "  -d, --database DATABASE  Set database type (sqlite|postgresql) [default: sqlite]"
    echo "  -b, --build             Force rebuild Docker images"
    echo "  -t, --detach            Run in detached mode"
    echo "  -h, --help              Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                                     # Development with SQLite (default)"
    echo "  $0 --env development                   # Development with SQLite (default)"
    echo "  $0 --env development --database postgresql  # Development with PostgreSQL"
    echo "  $0 --env production                    # Production with SQLite (default)"
    echo "  $0 --env production --database postgresql   # Production with PostgreSQL"
    echo "  $0 --build                             # Deployment with rebuild"
}

# Default values
ENVIRONMENT="development"
DATABASE="sqlite"
BUILD_FLAG=""
DETACH_FLAG=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--env)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -d|--database)
            DATABASE="$2"
            shift 2
            ;;
        -b|--build)
            BUILD_FLAG="--build"
            shift
            ;;
        -t|--detach)
            DETACH_FLAG="-d"
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Validate environment
if [[ "$ENVIRONMENT" != "development" && "$ENVIRONMENT" != "production" ]]; then
    print_error "Invalid environment: $ENVIRONMENT. Must be 'development' or 'production'"
    exit 1
fi

# Validate database
if [[ "$DATABASE" != "sqlite" && "$DATABASE" != "postgresql" ]]; then
    print_error "Invalid database: $DATABASE. Must be 'sqlite' or 'postgresql'"
    exit 1
fi

# Determine compose file
if [[ "$ENVIRONMENT" == "production" ]]; then
    if [[ "$DATABASE" == "postgresql" ]]; then
        COMPOSE_FILE="docker-compose.yml"
    else
        COMPOSE_FILE="docker-compose.sqlite.yml"
    fi
else
    if [[ "$DATABASE" == "postgresql" ]]; then
        COMPOSE_FILE="docker-compose.dev.yml"
    else
        COMPOSE_FILE="docker-compose.dev.sqlite.yml"
    fi
fi

# Check if compose file exists
if [[ ! -f "$COMPOSE_FILE" ]]; then
    print_error "Compose file not found: $COMPOSE_FILE"
    exit 1
fi

print_status "Starting deployment..."
print_status "Environment: $ENVIRONMENT"
print_status "Database: $DATABASE"
print_status "Compose file: $COMPOSE_FILE"

# Stop existing containers
print_status "Stopping existing containers..."
docker-compose -f "$COMPOSE_FILE" down

# Build images if requested
if [[ -n "$BUILD_FLAG" ]]; then
    print_status "Building Docker images..."
    docker-compose -f "$COMPOSE_FILE" build --no-cache
fi


print_status "Starting services..."
docker-compose -f "$COMPOSE_FILE" up $DETACH_FLAG

if [[ -n "$DETACH_FLAG" ]]; then
    print_success "Services started in detached mode"
    print_status "Use 'docker-compose -f $COMPOSE_FILE logs -f' to view logs"
    print_status "Use 'docker-compose -f $COMPOSE_FILE down' to stop services"
else
    print_success "Deployment completed!"
    print_status "Database: $DATABASE"
    print_status "API available at: http://localhost:8000"
    print_status "API docs at: http://localhost:8000/docs"
fi
