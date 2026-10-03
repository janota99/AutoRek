// Creates the janota_fin database with three validated collections and loads the sample documents.
//
// Run from the repository root:
//   mongosh "mongodb://localhost:27017" --file mongodb/seed.mongodb.js
//
// It DROPS and recreates reviews, plans and tools in the janota_fin database, so it is safe to run
// again, and should not be pointed at a database holding anything you want to keep. The website is
// not connected to MongoDB yet; this only establishes the data structure.

const fs = require("fs");

const database = db.getSiblingDB("janota_fin");

function readJson(file) {
  return EJSON.parse(fs.readFileSync(file, "utf8"));  // understands {"$oid": ...} and {"$date": ...}
}

for (const name of ["reviews", "plans", "tools"]) {
  database.getCollection(name).drop();
  database.createCollection(name, {
    validator: readJson(`mongodb/schemas/${name}.schema.json`),  // { $jsonSchema: {...} }
    validationLevel: "strict",
    validationAction: "error",
  });
  const result = database.getCollection(name).insertMany(readJson(`mongodb/${name}.json`));
  print(`${name}: inserted ${Object.keys(result.insertedIds).length} document(s)`);
}

// A few queries that exercise each type: number, boolean and null.
print("\nReviews rated 4 or higher that the reviewer would recommend:");
printjson(database.reviews.find({ rating: { $gte: 4 }, recommend: true }, { name: 1, rating: 1, _id: 0 }).toArray());

print("Reviews with no role (null):");
printjson(database.reviews.find({ role: null }, { name: 1, _id: 0 }).toArray());

print("Plans with a list price, cheapest first:");
printjson(database.plans.find({ monthlyPriceUsd: { $ne: null } }, { name: 1, monthlyPriceUsd: 1 }).sort({ monthlyPriceUsd: 1 }).toArray());

print("Prototype tools:");
printjson(database.tools.find({ isPrototype: true }, { title: 1, badge: 1 }).toArray());
