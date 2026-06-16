// Shared declarations and helpers for the path tracer's fragment passes.
// Pulled in by an include directive *after* the `#version` line of each
// fragment shader, so this file must not declare a version itself.
//
// Conventions documented here once and relied on throughout:
//
//  * World space is voxel space: a voxel at integer coord c occupies the unit
//    cube [c, c+1). u_dims is the grid size in voxels.
//  * trace_voxel / march_medium return a face normal that ALWAYS points against
//    the ray (toward the side the ray approached from), i.e. outward from the
//    surface that was hit.
//  * Irradiance cache atlas: one texel per (voxel, face). The six face slabs are
//    stacked vertically — see atlas_coord(). face 0:+X 1:-X 2:+Y 3:-Y 4:+Z 5:-Z.
//  * Material texels (u_materials, MAT_TEXELS columns per material row):
//      texel0 = albedo.rgb, useVoxelColor flag in .a
//      texel1 = (metallic, roughness, transmission, ior)
//      texel2 = emission.rgb, emissionStrength in .a
//      texel3 = (emissionUsesVoxelColor flag, material flags, 0, 0)

uniform sampler3D  u_vol_color;     // per-voxel RGBA (alpha = filled)
uniform usampler3D u_vol_mat;       // per-voxel material row index
uniform sampler2D  u_materials;     // material table (see header)
uniform sampler2D  u_env;           // equirectangular environment map
uniform sampler2D  u_gi;            // irradiance atlas (prev cache / current cache)

uniform ivec3 u_dims;
uniform int   u_max_steps;
uniform int   u_max_trans_depth;
uniform float u_gi_scale;
uniform float u_glass_density;

uniform int   u_env_mode;           // 0 ambient, 1 texture, 2 procedural sky
uniform float u_env_intensity;
uniform bool  u_env_flip_h;
uniform bool  u_env_flip_v;
uniform vec3  u_ambient;

const float PI = 3.14159265359;
const float EPS = 2e-3;

// -- random ------------------------------------------------------------------
uint pcg(inout uint state) {
    state = state * 747796405u + 2891336453u;
    uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
    return (word >> 22u) ^ word;
}
float random01(inout uint state) { return float(pcg(state)) / 4294967296.0; }

// -- colour / sampling -------------------------------------------------------
vec3 to_linear(vec3 c) { return pow(c, vec3(2.2)); }

void build_basis(vec3 n, out vec3 tangent, out vec3 bitangent) {
    tangent = normalize(cross(abs(n.z) < 0.999 ? vec3(0, 0, 1) : vec3(1, 0, 0), n));
    bitangent = cross(n, tangent);
}
vec3 cosine_sample(vec3 n, inout uint rng) {
    float u1 = random01(rng), u2 = random01(rng);
    float radius = sqrt(u1), phi = 2.0 * PI * u2;
    vec3 tangent, bitangent; build_basis(n, tangent, bitangent);
    return normalize(tangent * (radius * cos(phi))
                   + bitangent * (radius * sin(phi))
                   + n * sqrt(max(0.0, 1.0 - u1)));
}
vec3 ggx_sample(vec3 n, float roughness, inout uint rng) {
    float a = max(roughness * roughness, 1e-3);
    float u1 = random01(rng), u2 = random01(rng);
    float phi = 2.0 * PI * u1;
    float cosTheta = sqrt((1.0 - u2) / (1.0 + (a * a - 1.0) * u2));
    float sinTheta = sqrt(max(0.0, 1.0 - cosTheta * cosTheta));
    vec3 tangent, bitangent; build_basis(n, tangent, bitangent);
    return normalize(tangent * (sinTheta * cos(phi))
                   + bitangent * (sinTheta * sin(phi))
                   + n * cosTheta);
}

// -- BRDF terms --------------------------------------------------------------
float g_schlick(float ndx, float k) { return ndx / (ndx * (1.0 - k) + k); }
float smith_g(float ndv, float ndl, float roughness) {
    float k = (roughness * roughness) * 0.5;
    return g_schlick(ndv, k) * g_schlick(ndl, k);
}
vec3 fresnel_schlick(float cosTheta, vec3 f0) {
    return f0 + (1.0 - f0) * pow(1.0 - cosTheta, 5.0);
}
// Scalar Schlick reflectance for a dielectric interface of the given IOR ratio.
float fresnel_dielectric(float cosTheta, float ior) {
    float r0 = (1.0 - ior) / (1.0 + ior);
    r0 *= r0;
    return r0 + (1.0 - r0) * pow(1.0 - clamp(cosTheta, 0.0, 1.0), 5.0);
}

// -- environment -------------------------------------------------------------
vec3 procedural_sky(vec3 d) {
    vec3 horizon = vec3(0.90, 0.94, 1.00);
    vec3 zenith  = vec3(0.22, 0.42, 0.85);
    vec3 ground  = vec3(0.34, 0.32, 0.30);
    vec3 col = (d.z >= 0.0) ? mix(horizon, zenith, pow(clamp(d.z, 0.0, 1.0), 0.55))
                            : mix(horizon, ground, clamp(-d.z * 2.5, 0.0, 1.0));
    vec3 sun = normalize(vec3(0.55, 0.35, 0.75));
    float s = max(dot(d, sun), 0.0);
    col += vec3(1.0, 0.96, 0.85) * pow(s, 1500.0) * 8.0;
    col += vec3(1.0, 0.90, 0.72) * pow(s, 12.0) * 0.25;
    return col;
}
vec3 sample_env(vec3 d) {
    if (u_env_mode == 0) return u_ambient;
    if (u_env_mode == 2) return procedural_sky(d) * u_env_intensity;
    float u = atan(d.y, d.x) / (2.0 * PI) + 0.5;
    float v = acos(clamp(d.z, -1.0, 1.0)) / PI;
    // v=0 at the zenith. Image row 0 (the sky) maps to GL t=0, so the un-flipped
    // mapping is texV = v: looking up samples the top of the map. (The flip is for
    // sources whose vertical axis is already inverted.)
    float texU = u_env_flip_h ? 1.0 - u : u;
    float texV = u_env_flip_v ? 1.0 - v : v;
    return texture(u_env, vec2(texU, texV)).rgb * u_env_intensity;
}

// -- voxel volume access -----------------------------------------------------
bool in_bounds(ivec3 c) {
    return all(greaterThanEqual(c, ivec3(0))) && all(lessThan(c, u_dims));
}
bool filled(ivec3 c) { return texelFetch(u_vol_color, c, 0).a > 0.001; }
int  material_row(ivec3 c) { return int(texelFetch(u_vol_mat, c, 0).r); }
float transmission_at(ivec3 c) {
    return texelFetch(u_materials, ivec2(1, material_row(c)), 0).z;
}

// -- directional irradiance cache --------------------------------------------
int face_of(vec3 n) {
    vec3 a = abs(n);
    if (a.x >= a.y && a.x >= a.z) return n.x > 0.0 ? 0 : 1;
    if (a.y >= a.z)               return n.y > 0.0 ? 2 : 3;
    return n.z > 0.0 ? 4 : 5;
}
vec3 face_normal(int f) {
    if (f == 0) return vec3( 1, 0, 0);  if (f == 1) return vec3(-1, 0, 0);
    if (f == 2) return vec3( 0, 1, 0);  if (f == 3) return vec3( 0,-1, 0);
    if (f == 4) return vec3( 0, 0, 1);  return vec3( 0, 0,-1);
}
ivec2 atlas_coord(ivec3 v, int face) {
    return ivec2(v.x, v.y + v.z * u_dims.y + face * (u_dims.y * u_dims.z));
}

// -- material ----------------------------------------------------------------
struct Mat {
    vec3 albedo;
    float metallic;
    float roughness;
    float transmission;
    float ior;
    vec3 emission;
};
Mat fetch_material(ivec3 v) {
    int row = material_row(v);
    vec4 texel0 = texelFetch(u_materials, ivec2(0, row), 0);
    vec4 texel1 = texelFetch(u_materials, ivec2(1, row), 0);
    vec4 texel2 = texelFetch(u_materials, ivec2(2, row), 0);
    vec4 texel3 = texelFetch(u_materials, ivec2(3, row), 0);
    vec3 voxelColor = to_linear(texelFetch(u_vol_color, v, 0).rgb);
    Mat m;
    m.albedo = (texel0.a > 0.5) ? voxelColor * to_linear(texel0.rgb) : to_linear(texel0.rgb);
    m.metallic = texel1.x;
    m.roughness = clamp(texel1.y, 0.02, 1.0);
    m.transmission = texel1.z;
    m.ior = max(texel1.w, 1.0);
    m.emission = ((texel3.x > 0.5) ? voxelColor : to_linear(texel2.rgb)) * texel2.w;
    return m;
}
// Diffuse outgoing radiance of voxel v as seen from the side of face normal n
// (reads that face's cached irradiance). Emission is omnidirectional.
vec3 voxel_outgoing_radiance(ivec3 v, vec3 n) {
    Mat m = fetch_material(v);
    vec3 irradiance = texelFetch(u_gi, atlas_coord(v, face_of(n)), 0).rgb;
    return m.emission + m.albedo * irradiance;
}

// -- traversal ---------------------------------------------------------------
// 3D-DDA traversal. Returns the first filled cell along the ray other than
// `ignoreCell` (pass ivec3(-9999) to ignore nothing). `hitNormal` points against
// the ray (outward from the hit face). See header for the convention.
//
// `skipTransmissive`: when true, transmissive (glass) cells are treated as empty
// and the ray passes straight through them. The camera passes leave it false so
// glass is a visible surface; the GI gather sets it true so indirect light reaches
// surfaces behind/under glass (otherwise they cache zero irradiance and render
// black through the glass).
bool trace_voxel(vec3 rayOrigin, vec3 rayDir, ivec3 ignoreCell, bool skipTransmissive,
                 out ivec3 hitCell, out vec3 hitNormal, out float hitDistance) {
    // guard against exact-zero direction components (1/0, sign(0))
    if (abs(rayDir.x) < 1e-6) rayDir.x = 1e-6;
    if (abs(rayDir.y) < 1e-6) rayDir.y = 1e-6;
    if (abs(rayDir.z) < 1e-6) rayDir.z = 1e-6;
    vec3 invDir = 1.0 / rayDir;
    vec3 tLo = (vec3(0.0) - rayOrigin) * invDir;
    vec3 tHi = (vec3(u_dims) - rayOrigin) * invDir;
    vec3 tNear = min(tLo, tHi);
    vec3 tFar  = max(tLo, tHi);
    float tEnter = max(max(tNear.x, tNear.y), tNear.z);
    float tExit  = min(min(tFar.x, tFar.y), tFar.z);
    if (tExit < max(tEnter, 0.0)) return false;

    float t = max(tEnter, 0.0) + 1e-4;
    ivec3 cell = clamp(ivec3(floor(rayOrigin + rayDir * t)), ivec3(0), u_dims - 1);
    vec3 step = sign(rayDir);
    vec3 tDelta = abs(invDir);
    vec3 tMax = (vec3(cell) + max(step, 0.0) - rayOrigin) * invDir;
    // Axis crossed to enter `cell` (valid when the ray started outside the box).
    int axis = (tNear.x > tNear.y) ? ((tNear.x > tNear.z) ? 0 : 2)
                                   : ((tNear.y > tNear.z) ? 1 : 2);
    bool startedInside = tEnter < 0.0;

    for (int i = 0; i < u_max_steps; i++) {
        if (!in_bounds(cell)) return false;
        bool hittable = filled(cell) && !(skipTransmissive && transmission_at(cell) > 0.0);
        if (hittable && any(notEqual(cell, ignoreCell))) {
            hitNormal = vec3(0.0);
            if (startedInside && i == 0) {
                // Degenerate: origin already inside a solid cell. Best-effort
                // normal opposing the ray's dominant axis.
                vec3 a = abs(rayDir);
                int d = (a.x >= a.y && a.x >= a.z) ? 0 : (a.y >= a.z ? 1 : 2);
                hitNormal[d] = -step[d];
            } else {
                hitNormal[axis] = -step[axis];
            }
            hitCell = cell;
            hitDistance = t;
            return true;
        }
        if (tMax.x < tMax.y && tMax.x < tMax.z)      { cell.x += int(step.x); t = tMax.x; tMax.x += tDelta.x; axis = 0; }
        else if (tMax.y < tMax.z)                    { cell.y += int(step.y); t = tMax.y; tMax.y += tDelta.y; axis = 1; }
        else                                         { cell.z += int(step.z); t = tMax.z; tMax.z += tDelta.z; axis = 2; }
    }
    return false;
}

// March a ray that starts INSIDE a homogeneous voxel medium (every cell shares
// `mediumRow`). Walks straight through internal medium faces and stops at the
// first cell that is empty or a different material — the block's exit interface.
// Returns the distance travelled to that interface, its outward face normal
// (pointing along the ray, out of the medium), the cell just beyond it, and
// whether that cell is empty (vs a different solid). This treats one connected
// run of a single material as a single optical block.
void march_medium(vec3 rayOrigin, vec3 rayDir, int mediumRow,
                  out float exitDistance, out vec3 exitNormal,
                  out ivec3 beyondCell, out bool exitToEmpty) {
    if (abs(rayDir.x) < 1e-6) rayDir.x = 1e-6;
    if (abs(rayDir.y) < 1e-6) rayDir.y = 1e-6;
    if (abs(rayDir.z) < 1e-6) rayDir.z = 1e-6;
    vec3 invDir = 1.0 / rayDir;
    ivec3 cell = clamp(ivec3(floor(rayOrigin)), ivec3(0), u_dims - 1);
    vec3 step = sign(rayDir);
    vec3 tDelta = abs(invDir);
    vec3 tMax = (vec3(cell) + max(step, 0.0) - rayOrigin) * invDir;
    float t = 0.0;
    for (int i = 0; i < u_max_steps; i++) {
        int axis;
        if (tMax.x < tMax.y && tMax.x < tMax.z)      { t = tMax.x; cell.x += int(step.x); tMax.x += tDelta.x; axis = 0; }
        else if (tMax.y < tMax.z)                    { t = tMax.y; cell.y += int(step.y); tMax.y += tDelta.y; axis = 1; }
        else                                         { t = tMax.z; cell.z += int(step.z); tMax.z += tDelta.z; axis = 2; }
        bool stillMedium = in_bounds(cell) && filled(cell) && material_row(cell) == mediumRow;
        if (!stillMedium) {
            exitDistance = t;
            exitNormal = vec3(0.0);
            exitNormal[axis] = step[axis];           // along travel, out of medium
            beyondCell = cell;
            exitToEmpty = !(in_bounds(cell) && filled(cell));
            return;
        }
    }
    exitDistance = t;
    exitNormal = -normalize(rayDir);
    beyondCell = cell;
    exitToEmpty = true;
}
