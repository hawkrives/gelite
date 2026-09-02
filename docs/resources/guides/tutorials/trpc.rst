.. _ref_guide_trpc:

====
tRPC
====

:edb-alt-title: Integrating Gel with tRPC

This guide explains how to integrate **Gel** with **tRPC** for a modern,
type-safe API. We'll cover setting up database interactions, API routing,
and implementing authentication, all while ensuring type safety across the
client and server.

You can reference the following repositories for more context:

- `create-t3-turbo-gel <https://github.com/geldata/create-t3-turbo-gel>`_ -
  A monorepo template using the `T3 stack <https://init.tips/>`_,
  `Turborepo <https://turbo.build/>`_, and Gel.
- `LookFeel Project <https://github.com/LewTrn/lookfeel>`_ - A real-world
  example using **Gel** and **tRPC**.

Step 1: Gel setup
=================

|Gel| will serve as the database layer for your application.

Install and initialize Gel
--------------------------

To initialize **Gel**, run the following command using your preferred
package manager:

.. code-block:: bash

   $ pnpm dlx gel project init # or `npx gel project init`

This will create a Gel project and set up a schema to start with.

Define the Gel Schema
---------------------

The previous command generated a schema file in the ``dbschema`` directory.

Here's an example schema that defines a ``User`` model:

.. code-block:: sdl
   :caption: dbschema/default.gel

   module default {
     type User {
       required name: str;
       required email: str;
     }
   }

Apply schema migrations
-----------------------

Once schema changes are made, apply migrations with:

.. code-block:: bash

   $ pnpm dlx gel migration create # or npx gel migration create
   $ pnpm dlx gel migration apply # or npx gel migration apply

Step 2: Configure Gel Client
============================

To interact with **Gel** from your application, you need to configure the
client.

Install Gel Client
------------------

First, install the **Gel** client using your package manager:

.. code-block:: bash

   $ pnpm add gel
   $ # or yarn add gel
   $ # or npm install gel
   $ # or bun add gel

Then, create a client instance in a ``gel.ts`` file:

.. code-block:: typescript
   :caption: src/gel.ts

   import { createClient } from 'gel';

   const gelClient = createClient();
   export default gelClient;

This client will be used to interact with the database and execute queries.

Step 3: tRPC setup
==================

**tRPC** enables type-safe communication between the frontend and
backend.

Install tRPC dependencies
-------------------------

Install the required tRPC dependencies:

.. code-block:: bash

   $ pnpm add @trpc/server @trpc/client
   $ # or yarn add @trpc/server @trpc/client
   $ # or npm install @trpc/server @trpc/client
   $ # or bun add @trpc/server @trpc/client

If you're using React and would like to use React Query with tRPC, also
install a wrapper around the `@tanstack/react-query <https://tanstack.com/query/latest>`_.

.. code-block:: bash

   $ pnpm add @trpc/react-query
   $ # or yarn add @trpc/react-query
   $ # or npm install @trpc/react-query
   $ # or bun add @trpc/react-query

Define the tRPC Router
-----------------------

Here's how to define a simple tRPC query that interacts with **Gel**:

.. code-block:: typescript
   :caption: server/routers/_app.ts

   import { initTRPC } from '@trpc/server';
   import gelClient from './gel';

   const t = initTRPC.create();

   export const appRouter = t.router({
     getUsers: t.procedure.query(async () => {
       const users = await gelClient.query('SELECT User { name, email }');
       return users;
     }),
   });

   export type AppRouter = typeof appRouter;

This example defines a query that fetches user data from Gel, ensuring
type safety in both the query and response.

Step 4: Use tRPC Client
========================

Now that the server is set up, you can use the tRPC client to interact with
the API from the frontend. We will demonstrate how to integrate tRPC with
**Next.js** and **Express**.

With Next.js
------------

If you're working with **Next.js**, here's how to integrate **tRPC**:

Create a tRPC API Handler
~~~~~~~~~~~~~~~~~~~~~~~~~

Inside ``api/trpc/[trpc].ts``, create the following handler to connect
**tRPC** with Next.js:

.. code-block:: typescript
   :caption: pages/api/trpc/[trpc].ts

   import { createNextApiHandler } from '@trpc/server/adapters/next';
   import { appRouter } from '../../../server/routers/_app';

   export default createNextApiHandler({
     router: appRouter,
   });

Create a tRPC Client
~~~~~~~~~~~~~~~~~~~~

Next, create a **tRPC** client to interact with the API:

.. code-block:: typescript
   :caption: utils/trpc.ts

   import { createTRPCReact } from "@trpc/react-query";
   import { AppRouter } from './routers/_app';

   export const api = createTRPCReact<AppRouter>();

Client-Side Usage in Next.js
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can then use **tRPC** hooks to query the API from the client:

.. code-block:: typescript
   :caption: components/UsersComponent.tsx

   import { trpc } from '../utils/trpc';

   const UsersComponent = () => {
     const { data, isLoading } = trpc.getUsers.useQuery();

     if (isLoading) return <div>Loading...</div>;

     return (
       <div>
         {data?.map(user => (
           <p key={user.email}>{user.name}</p>
         ))}
       </div>
     );
   };

   export default UsersComponent;

Alternative Path: Use tRPC with Express
---------------------------------------

If you're not using **Next.js**, here's how you can integrate **tRPC** with
**Express**.

Set up Express server with tRPC
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Here's how you can create an Express server and integrate **tRPC**:

.. code-block:: typescript

   import express from 'express';
   import { appRouter } from './routers/_app';
   import * as trpcExpress from '@trpc/server/adapters/express';

   const app = express();

   app.use(
     '/trpc',
     trpcExpress.createExpressMiddleware({
       router: appRouter,
     })
   );

   app.listen(4000, () => {
     console.log('Server is running on port 4000');
   });

Client-side usage
-----------------

In non-Next.js apps, use the tRPC client to interact with the server:

.. code-block:: typescript

   import { createTRPCClient, httpBatchLink } from '@trpc/client';
   import { AppRouter } from './routers/_app';

   const trpc = createTRPCClient<AppRouter>({
     links: [
       httpBatchLink({
         url: 'http://localhost:4000/trpc',
       }),
     ],
   });

   async function fetchUsers() {
     const users = await trpc.getUsers.query();
     console.log(users);
   }

Conclusion
----------

You can also reference these projects for further examples:

- `create-t3-turbo-gel <https://github.com/geldata/create-t3-turbo-gel>`_ -
  A monorepo template using the `T3 stack <https://init.tips/>`_,
  `Turborepo <https://turbo.build/>`_, and Gel.
- `LookFeel Project <https://github.com/LewTrn/lookfeel>`_ - A real-world
  example using **Gel** and **tRPC**.
