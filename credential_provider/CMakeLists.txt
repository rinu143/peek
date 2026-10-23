# Peek Face Engine - Phase 5 Windows Credential Provider CMake Build Configuration
cmake_minimum_required(VERSION 3.15)
project(PeekCredentialProvider LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    add_compile_options(/W4 /EHsc /utf-8 /DUNICODE /D_UNICODE)
endif()

set(SOURCES
    Dll.cpp
    PeekProvider.cpp
    PeekCredential.cpp
    PeekPipeClient.cpp
    Serialization.cpp
    SecretVault.cpp
    TileAnimator.cpp
    resources.rc
    credential_provider.def
)

set(HEADERS
    common.h
    guid.h
    resource.h
    PeekProvider.h
    PeekCredential.h
    PeekPipeClient.h
    Serialization.h
    SecretVault.h
    TileAnimator.h
)

add_library(PeekCredentialProvider SHARED
    ${SOURCES}
    ${HEADERS}
)

target_link_libraries(PeekCredentialProvider PRIVATE
    Secur32
    Shlwapi
    Advapi32
    Wtsapi32
    Crypt32
    Ole32
    User32
    Gdi32
    Gdiplus
    Userenv
    Propsys
    Shell32
)

set_target_properties(PeekCredentialProvider PROPERTIES
    OUTPUT_NAME "PeekCredentialProvider"
    LINK_FLAGS "/DEF:\"${CMAKE_CURRENT_SOURCE_DIR}/credential_provider.def\""
)

add_executable(test_autoselect
    ../test/test_autoselect.cpp
    PeekProvider.cpp
    PeekCredential.cpp
    PeekPipeClient.cpp
    Serialization.cpp
    SecretVault.cpp
    TileAnimator.cpp
    resources.rc
)

target_compile_definitions(test_autoselect PRIVATE
    PEEK_TEST_BUILD
    PEEK_DIAG
)

target_include_directories(test_autoselect PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(test_autoselect PRIVATE
    Secur32
    Shlwapi
    Advapi32
    Wtsapi32
    Crypt32
    Ole32
    User32
    Gdi32
    Gdiplus
    Userenv
    Propsys
    Shell32
)

add_executable(test_secret_vault
    ../test/test_secret_vault.cpp
    SecretVault.cpp
)

target_include_directories(test_secret_vault PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(test_secret_vault PRIVATE
    Secur32
    Shlwapi
    Advapi32
    Wtsapi32
    Crypt32
    Ole32
)
