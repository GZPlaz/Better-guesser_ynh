#!/bin/bash

# Copy the app code shipped in this package (no upstream tarball) into the install dir.
_install_app_code() {
    mkdir -p "$install_dir"
    cp ../src/app.py ../src/index.html "$install_dir/"
    chown -R "$app:www-data" "$install_dir"
    chmod -R o-rwx "$install_dir"
}
