#!/bin/sh

# Using a list of packages, define a configuration for apt that allows the
# listed packages to be obtained for installation.
#
# Usage: add-repositories.sh <apt base directory>

# Import common definitions.

. $(dirname "$0")/repositories.sh

APT_PATH=${1:-"/etc/apt"}

# Add repository details to an apt configuration in the indicated directory.
#
# Usage: add_repositories <apt base directory>

add_repositories()
{
    APT_SOURCES="$1/sources.list.d"
    APT_LIST="${APT_SOURCES}/new-packages-testing.list"
    APT_GPG_TRUSTED_DIR="$1/trusted.gpg.d"

    # Create the configuration resources.

    if [ ! -e "$APT_SOURCES" ] ; then
        mkdir -p "$APT_SOURCES"
    fi

    echo -n > "$APT_LIST"

    if [ ! -e "$APT_GPG_TRUSTED_DIR" ] ; then
        mkdir -p "$APT_GPG_TRUSTED_DIR"
    fi

    # Add configuration entries and public keys for dependencies.

    for PACKAGE in $SALSA_PACKAGES_LIST ; do

        # Define the repository location.

        URL=$(repository_location "$PACKAGE")

        # Generate an obvious failure if there is no appropriate repository.
        # That will hopefully make troubleshooting easier when other jobs try
        # and fetch from this non-existent repository.

        if [ ! "$?" ] ; then
            echo "FAIL! $PACKAGE was not built successfully." >> "$APT_LIST"
        else
            echo "deb ${URL} unstable main" >> "$APT_LIST"

            # Obtain the public key for the repository. Keep redirection
            # support in place in case the server is doing something clever.

            curl -s -L -o "${APT_GPG_TRUSTED_DIR}/${PACKAGE}.asc" "${URL}/${PUBKEY_URL_SUFFIX}"
        fi
    done
}

# First, make sure that curl is available.

apt-get update
NON_INTERACTIVE=1 apt-get install -y curl

# Obtain the package and repository details.

add_repositories "$APT_PATH"

# Update the repository records.

apt-get update
